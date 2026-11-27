"""Tests for services/queue_service.py: the user-managed job queue.

The stage start functions are replaced by fakes that start a real
background job whose body waits on an Event, so the runner sees real job
records through jobs_service, and each test decides when a job ends."""
import threading

import pytest

import background_jobs
from jobs import job_launch, job_runner
import db
from engine_backends import local
from core import Line
from lib.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from services import (auth_service, queue_service as q, settings_service, transcribe_service,
                      translate_run_service)

pytestmark = pytest.mark.usefixtures("local_ollama_default")


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
        self.gpu = False
        self.transcribe_kwargs = []
        monkeypatch.setattr(transcribe_service, "start_transcribe_run", self._transcribe)
        monkeypatch.setattr(translate_run_service, "start_translate_run", self._translate)
        monkeypatch.setattr(local, "check_ollama_reachable", lambda url: False)

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
        if not job_launch.start_job(job_id, body, description="fake",
                                         gpu_touching=self.gpu):
            raise ConflictError("busy")
        self.started.append(job_id)
        self.any_started.set()
        return {"job_id": job_id}

    def _transcribe(self, drama_id, **kw):
        self.transcribe_kwargs.append(kw)
        return self._start(f"transcribe_{drama_id}")

    def _translate(self, drama_id, **kw):
        return self._start(f"translate_{drama_id}")

    def finish(self, job_id):
        self.gates.setdefault(job_id, threading.Event()).set()
        assert job_runner.wait_for_job_threads(WAIT, job_ids=[job_id])


@pytest.fixture
def jobs(isolated_db, monkeypatch):
    fake = FakeJobs(monkeypatch)
    yield fake
    for gate in list(fake.gates.values()):
        gate.set()
    job_runner.wait_for_job_threads(WAIT)


def _items():
    return q.get_queue(None)["items"]


def _status():
    return [(i["step"], i["status"]) for i in _items()]


def test_items_run_one_at_a_time_in_order(jobs):
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True, at_pc=True)
    q.add_item(None, b, "transcribe", True, at_pc=True)
    q.add_item(None, a, "translate", True, at_pc=True)
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
    q.add_item(None, a, "transcribe", True, at_pc=True)
    q.add_item(None, b, "transcribe", True, at_pc=True)
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


@pytest.mark.parametrize("adder", [None, 7])
def test_a_stop_on_failure_is_announced_once_to_the_items_adder(jobs, monkeypatch, adder):
    from services import notification_service
    told = []
    monkeypatch.setattr(notification_service, "notify_waiting",
                        lambda text, **kw: told.append({"text": text, **kw}))
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True, at_pc=True)
    q.add_item(None, b, "transcribe", True, at_pc=True)
    q.set_policy(None, True)
    q.start(None)
    jobs.fail.add(f"transcribe_{a}")
    q.tick()
    # Set once it runs: starting as a member needs a real account.
    with q._lock:
        doc = q._load()
        doc["items"][0]["user_id"] = adder
        q._save(doc)
    jobs.finish(f"transcribe_{a}")
    q.tick()
    q.tick()
    q.tick()
    text = "The queue stopped because an item failed. Start the queue to go on."
    assert told == [{"text": text, "push_text": text, "owner_user_id": adder,
                     "target": {"page": "jobs"}}]
    # The Jobs page keeps its own wording.
    assert q.get_queue(None)["notice"] == "Stopped because an item failed. Start the queue to go on."


def test_a_failed_item_without_the_stop_policy_announces_nothing(jobs, monkeypatch):
    from services import notification_service
    told = []
    monkeypatch.setattr(notification_service, "notify_waiting", lambda *a, **kw: told.append(a))
    a = _drama("A")
    q.add_item(None, a, "transcribe", True, at_pc=True)
    q.start(None)
    jobs.fail.add(f"transcribe_{a}")
    q.tick()
    jobs.finish(f"transcribe_{a}")
    q.tick()
    assert _items()[0]["status"] == "failed" and told == []


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
    q.add_item(None, paid, "translate", True, at_pc=True)
    q.add_item(None, free, "transcribe", True, at_pc=True)
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
    q.add_item(None, did, "translate", True, at_pc=True)
    q.start(None)
    q.tick()
    item = _items()[0]
    assert item["status"] == "skipped" and "above what is left" in item["reason"]
    assert jobs.started == []


def test_a_start_refusal_is_a_plain_skip_and_the_queue_moves_on(jobs):
    gone, ok = _drama("Gone"), _drama("Ok")
    q.add_item(None, gone, "transcribe", True, at_pc=True)
    q.add_item(None, ok, "transcribe", True, at_pc=True)
    db.delete_drama(gone)
    q.start(None)
    q.tick()
    q.tick()
    assert _items()[0]["status"] == "skipped"
    assert _items()[0]["reason"] == "Skipped: The title was deleted."
    assert jobs.started == [f"transcribe_{ok}"]


def test_pause_stops_new_starts_but_the_running_item_finishes(jobs):
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True, at_pc=True)
    q.add_item(None, b, "transcribe", True, at_pc=True)
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
    q.add_item(None, a, "transcribe", True, at_pc=True)
    q.add_item(None, b, "transcribe", True, at_pc=True)
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
    q.retry_item(None, state["items"][0]["id"], at_pc=True)
    assert _items()[0]["status"] == "waiting"


def test_restart_marks_a_passed_schedule_missed_and_never_runs_it(jobs):
    did = _drama()
    now = 1_900_000_000.0
    q.set_schedule(None, "02:00", now=now)
    q.add_item(None, did, "transcribe", True, at_pc=True)
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
    now = 1_900_000_000.0
    q.set_schedule(None, "23:30", now=now)
    q.add_item(None, a, "transcribe", True, at_pc=True)      # a start time is the start: adding doesn't run it
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
    # The schedule fired once and is gone: an item added later starts the idle queue itself.
    q.add_item(None, b, "transcribe", True, at_pc=True)
    q.tick(now=start_at + 86400 + 10)
    assert jobs.started == [f"transcribe_{a}", f"transcribe_{b}"]


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
    q.add_item(None, did, "export", True, at_pc=True)
    q.start(None)
    q.tick()
    assert _items()[0]["status"] == "done"
    art = artifact_service.get_artifact(did, "subtitle")
    with open(art["path"], encoding="utf-8") as f:
        assert "Hello" in f.read()
    assert "path" not in _items()[0]


def test_export_is_skipped_with_nothing_to_export(jobs):
    did = _drama()
    q.add_item(None, did, "export", True, at_pc=True)
    q.start(None)
    q.tick()
    assert _items()[0]["reason"] == "Skipped: There are no English lines to export yet."


def test_validation_rejects_unknown_titles_bad_steps_and_a_full_queue(jobs):
    did = _drama()
    with pytest.raises(NotFoundError):
        q.add_item(None, 999999, "transcribe", True, at_pc=True)
    with pytest.raises(InvalidInputError):
        q.add_item(None, did, "dub", True, at_pc=True)
    for _ in range(q.MAX_ITEMS):
        q.add_item(None, did, "export", True, at_pc=True)
    with pytest.raises(ConflictError):
        q.add_item(None, did, "export", True, at_pc=True)
    assert len(_items()) == q.MAX_ITEMS


def test_all_steps_adds_three_ordered_items_each_waiting(jobs):
    did = _drama(lines=(("你好", "Hello"),))
    q.add_all_steps(None, did, True, at_pc=True)
    assert [(i["step"], i["drama_id"], i["status"]) for i in _items()] == [
        ("transcribe", did, "waiting"), ("translate", did, "waiting"), ("export", did, "waiting")]
    assert q.get_queue(None)["state"] == "running"


def test_all_steps_adds_nothing_when_any_step_is_refused(jobs):
    with pytest.raises(NotFoundError):
        q.add_all_steps(None, 999999, True, at_pc=True)
    paid = _drama(engine="claude")
    with pytest.raises(ForbiddenError):
        q.add_all_steps(None, paid, False, at_pc=True)
    did = _drama()
    for _ in range(q.MAX_ITEMS - 2):
        q.add_item(None, did, "export", True, at_pc=True)
    with pytest.raises(ConflictError):
        q.add_all_steps(None, did, True, at_pc=True)
    assert len(_items()) == q.MAX_ITEMS - 2


def test_all_steps_stops_the_later_steps_after_a_failure_when_the_policy_says_so(jobs):
    did = _drama()
    q.set_policy(None, True)
    q.add_all_steps(None, did, True, at_pc=True)
    jobs.fail.add(f"transcribe_{did}")
    q.start(None)
    q.tick()
    jobs.finish(f"transcribe_{did}")
    q.tick()
    assert _status() == [("transcribe", "failed"), ("translate", "waiting"), ("export", "waiting")]
    assert jobs.started == [f"transcribe_{did}"]


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
        q.add_item(pa, shared, "transcribe", False, at_pc=True)     # visible, but not theirs
    q.add_item(pa, mine, "transcribe", False, at_pc=True)
    q.add_item(pb, shared, "transcribe", False, at_pc=True)
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
        q.add_item(member, did, "translate", False, at_pc=True)
    q.add_item(member, did, "transcribe", False, at_pc=True)     # a free step is fine
    state = q.add_item(None, did, "translate", True, at_pc=True)
    state["items"] = [i for i in state["items"] if i["step"] == "translate"]
    assert state["items"][0]["engine"] == "claude" and state["items"][0]["paid"] is True


def test_a_revoked_account_has_its_waiting_items_skipped(jobs):
    u = auth_service.add_user("a@example.com")
    auth_service.grant_permission(u["id"], "jobs.start")
    did = _drama("Mine", owner_user_id=u["id"], is_private=1)
    q.add_item({"user_id": u["id"], "is_admin": False, "permissions": ["jobs.start"]},
               did, "transcribe", False, at_pc=True)
    auth_service.revoke_permission(u["id"], "jobs.start")
    q.start(None)
    q.tick()
    assert _items()[0]["status"] == "skipped" and jobs.started == []


def test_reorder_remove_and_clear_finished(jobs):
    a, b, c = _drama("A"), _drama("B"), _drama("C")
    for did in (a, b, c):
        q.add_item(None, did, "transcribe", True, at_pc=True)
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
    q.add_item(None, did, "transcribe", True, at_pc=True)
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
               did, "transcribe", False, at_pc=True)
    q.start(None)
    q.tick()
    item = _items()[0]
    from services import ownership_service
    assert item["status"] == "skipped"
    assert item["reason"] == f"Skipped: {ownership_service._REPLACE_NEEDS_EDIT}"


def test_a_start_time_passed_long_ago_while_the_pc_slept_is_missed(jobs):
    # A sleeping PC runs no tick: on wake, hours late, the queue must not
    # start on its own any more than after a restart.
    did = _drama()
    now = 1_900_000_000.0
    q.set_schedule(None, "02:00", now=now)
    q.add_item(None, did, "transcribe", True, at_pc=True)
    start_at = q.get_queue(None)["schedule"]["start_at"]
    q.tick(now=start_at + q.SCHEDULE_LATE_SECONDS + 1)
    assert jobs.started == []
    state = q.get_queue(None)
    assert state["state"] == "idle" and state["schedule"]["missed"] is True


def test_a_start_time_a_little_late_still_fires(jobs):
    did = _drama()
    now = 1_900_000_000.0
    q.set_schedule(None, "02:00", now=now)
    q.add_item(None, did, "transcribe", True, at_pc=True)
    start_at = q.get_queue(None)["schedule"]["start_at"]
    q.tick(now=start_at + q.SCHEDULE_LATE_SECONDS - 1)
    assert jobs.started == [f"transcribe_{did}"]


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
    q.add_item(None, did, "translate", True, at_pc=True)
    q.start(None)
    q.tick()
    assert _items()[0]["reason"] == f"Skipped: {q._NO_CAP}"
    assert jobs.started == []


def test_a_restore_or_cleanup_hold_makes_the_queue_wait_not_skip(jobs):
    did = _drama()
    q.add_item(None, did, "transcribe", True, at_pc=True)
    q.start(None)
    assert job_launch.enter_maintenance()
    try:
        q.tick()
        assert _items()[0]["status"] == "waiting" and jobs.started == []
    finally:
        job_launch.exit_maintenance()
    q.tick()
    assert jobs.started == [f"transcribe_{did}"]


def test_a_lagging_job_record_does_not_end_the_item_early(jobs):
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True, at_pc=True)
    q.add_item(None, b, "transcribe", True, at_pc=True)
    q.start(None)
    q.tick()
    # As if the "running" write failed and the row kept an earlier run's status.
    db.save_job_record(f"transcribe_{a}", "done")
    q.tick()
    assert jobs.started == [f"transcribe_{a}"]
    assert _items()[0]["status"] == "running"


def test_a_claim_whose_outcome_was_never_written_is_interrupted(jobs):
    did = _drama()
    q.add_item(None, did, "transcribe", True, at_pc=True)
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
        q.add_item(member, did, "export", False, at_pc=True)
    with pytest.raises(ConflictError):
        q.add_item(member, did, "export", False, at_pc=True)
    q.add_item(None, did, "export", True, at_pc=True)     # the owner still can


def test_translate_passes_the_settings_default_locale_and_style_note(jobs, monkeypatch):
    seen = {}
    monkeypatch.setattr(translate_run_service, "start_translate_run",
                        lambda drama_id, **kw: seen.update(kw) or jobs._start(f"translate_{drama_id}"))
    real = settings_service.get
    monkeypatch.setattr(settings_service, "get", lambda k: {"default_locale": "en-GB",
                        "default_style_note": "Keep honorifics."}.get(k) or real(k))
    did = _drama()
    q.add_item(None, did, "translate", True, at_pc=True)
    q.start(None)
    q.tick()
    assert seen["locale"] == "en-GB" and seen["style_note"] == "Keep honorifics."


def _member_running_two_jobs(jobs):
    """A member who owns a title and already runs as many jobs as the cap
    allows, started directly (not from the queue)."""
    from jobs import user_caps
    from lib import acting
    member = auth_service.add_user("m@example.com")
    auth_service.grant_permission(member["id"], "jobs.start")
    principal = {"user_id": member["id"], "is_admin": False, "permissions": ["jobs.start"]}
    token = acting.bind_request()
    acting.note_acting_principal(principal)
    try:
        for n in range(user_caps.MAX_RUNNING_JOBS_PER_USER):
            jobs._start(f"direct_{n}")
    finally:
        acting.unbind_request(token)
    return principal


@pytest.mark.parametrize("gpu", [False, True])
def test_a_member_at_the_running_cap_never_holds_up_the_queue(jobs, gpu):
    principal = _member_running_two_jobs(jobs)
    jobs.gpu = gpu     # the item's job; the member's own two stay CPU jobs, both running
    theirs = _drama("Theirs", owner_user_id=principal["user_id"], is_private=1)
    owners = _drama("Owner's")
    q.add_item(principal, theirs, "transcribe", False, at_pc=True)
    q.add_item(None, owners, "transcribe", True, at_pc=True)
    q.start(None)

    q.tick()
    first, second = _items()
    assert first["status"] == "waiting" and "other jobs" in first["reason"]
    assert second["status"] == "running"
    assert f"transcribe_{theirs}" not in jobs.started
    assert background_jobs.get_status(f"transcribe_{theirs}") is None   # never queued

    jobs.finish("direct_0")
    jobs.finish(f"transcribe_{owners}")
    q.tick()
    q.tick()
    assert _items()[0]["status"] == "running" and _items()[0]["reason"] is None
    assert f"transcribe_{theirs}" in jobs.started
    assert q.get_queue(None)["state"] == "running"


def _member(email="m@example.com"):
    member = auth_service.add_user(email)
    auth_service.grant_permission(member["id"], "jobs.start")
    return {"user_id": member["id"], "is_admin": False, "permissions": ["jobs.start"]}


def test_a_nearly_full_drive_keeps_a_members_items_waiting_instead_of_skipping_them(jobs, monkeypatch):
    from collections import namedtuple
    from jobs import user_caps
    usage = namedtuple("usage", "total used free")
    free = {"gb": 5}
    monkeypatch.setattr(user_caps.shutil, "disk_usage",
                        lambda _p: usage(100 << 30, 0, free["gb"] << 30))
    principal = _member()
    a = _drama("A", owner_user_id=principal["user_id"], is_private=1)
    b = _drama("B", owner_user_id=principal["user_id"], is_private=1)
    owners = _drama("Owner's")
    q.add_item(principal, a, "transcribe", False, at_pc=True)
    q.add_item(principal, b, "transcribe", False, at_pc=True)
    q.add_item(None, owners, "transcribe", True, at_pc=True)
    q.start(None)

    t0 = q._clock()
    q.tick(now=t0)
    first, second, third = _items()
    assert first["status"] == second["status"] == "waiting"
    assert "almost out of disk space" in first["reason"] and "almost out of disk space" in second["reason"]
    assert third["status"] == "running"      # the owner's jobs are never held back

    jobs.finish(f"transcribe_{owners}")
    tries = len(jobs.concurrent)
    q.tick(now=t0 + 10)       # every member's items rest a while rather than being retried each tick
    assert len(jobs.concurrent) == tries and _items()[0]["status"] == "waiting"
    free["gb"] = 50
    q.tick(now=t0 + q.DISK_LOW_RETRY_SECONDS + 1)
    assert _items()[0]["status"] == "running" and _items()[0]["reason"] is None


def test_a_member_past_their_daily_limit_waits_for_tomorrow_without_retrying_every_tick(jobs):
    from jobs import user_caps
    principal = _member()
    uid = principal["user_id"]
    user_caps.set_jobs_per_day(uid, 1)
    user_caps.count_daily_start(uid)
    a = _drama("A", owner_user_id=uid, is_private=1)
    b = _drama("B", owner_user_id=uid, is_private=1)
    q.add_item(principal, a, "transcribe", False, at_pc=True)
    q.add_item(principal, b, "transcribe", False, at_pc=True)
    q.start(None)

    t0 = q._clock()
    q.tick(now=t0)
    tries = len(jobs.concurrent)
    assert tries >= 1 and jobs.started == []
    assert all(i["status"] == "waiting" and "job limit" in i["reason"] and "tomorrow" in i["reason"]
               for i in _items())
    q.tick(now=t0 + 60)
    q.tick(now=t0 + 120)
    assert len(jobs.concurrent) == tries     # resting: no start is tried
    assert q.get_queue(None)["state"] == "running"

    q.cancel_item(principal, _items()[1]["id"])
    q.retry_item(principal, _items()[1]["id"], at_pc=True)
    with q._lock:
        assert [i["not_before"] for i in q._load()["items"]][1] is None    # Retry tries it at once

    user_caps.set_jobs_per_day(uid, None)      # the PC owner lifts the limit
    q.tick(now=t0 + q.DAILY_LIMIT_RETRY_SECONDS + 1)
    assert _items()[0]["status"] == "running" and jobs.started == [f"transcribe_{a}"]


def test_a_full_drive_does_not_cut_short_a_members_daily_limit_rest(jobs, monkeypatch):
    from collections import namedtuple
    from jobs import user_caps
    usage = namedtuple("usage", "total used free")
    free = {"gb": 50}
    monkeypatch.setattr(user_caps.shutil, "disk_usage",
                        lambda _p: usage(100 << 30, 0, free["gb"] << 30))
    capped, other = _member("capped@example.com"), _member("other@example.com")
    user_caps.set_jobs_per_day(capped["user_id"], 1)
    user_caps.count_daily_start(capped["user_id"])
    q.add_item(capped, _drama("A", owner_user_id=capped["user_id"], is_private=1), "transcribe", False, at_pc=True)
    q.start(None)
    t0 = q._clock()
    q.tick(now=t0)
    with q._lock:
        rest_until = q._load()["items"][0]["not_before"]
    assert rest_until == t0 + q.DAILY_LIMIT_RETRY_SECONDS

    free["gb"] = 5
    q.add_item(other, _drama("B", owner_user_id=other["user_id"], is_private=1), "transcribe", False, at_pc=True)
    q.tick(now=t0 + 1)
    with q._lock:
        capped_item, other_item = q._load()["items"]
    assert capped_item["not_before"] == rest_until and "tomorrow" in capped_item["reason"]
    assert "almost out of disk space" in other_item["reason"]


def test_members_together_cannot_lock_the_owner_out_of_the_queue(jobs):
    members = [_member(f"m{n}@example.com") for n in range(3)]
    titles = [_drama(f"M{n}", owner_user_id=m["user_id"], is_private=1) for n, m in enumerate(members)]
    share = q.MAX_MEMBER_ITEMS // 2
    for member, did in zip(members[:2], titles[:2]):
        for _ in range(share):
            q.add_item(member, did, "export", False, at_pc=True)
    with pytest.raises(ConflictError) as err:
        q.add_item(members[2], titles[2], "export", False, at_pc=True)
    assert f"at most {q.MAX_MEMBER_ITEMS} items in the queue together" in err.value.message
    owners = _drama("Owner's")
    for _ in range(q.MAX_ITEMS - q.MAX_MEMBER_ITEMS):
        q.add_item(None, owners, "export", True, at_pc=True)
    assert len(_items()) == q.MAX_ITEMS


def test_a_deferred_item_alone_keeps_the_queue_running(jobs):
    principal = _member_running_two_jobs(jobs)
    theirs = _drama("Theirs", owner_user_id=principal["user_id"], is_private=1)
    q.add_item(principal, theirs, "transcribe", False, at_pc=True)
    q.start(None)
    q.tick()
    assert _items()[0]["status"] == "waiting"
    assert q.get_queue(None)["state"] == "running"
    jobs.finish("direct_1")
    q.tick()
    assert _items()[0]["status"] == "running"


def test_bulk_add_queues_each_title_and_skips_the_refused_with_a_reason(jobs):
    a = _drama("A")
    b = _drama("B")
    paid = _drama("Paid", engine="claude")
    out = q.add_bulk(None, [a, 999999, paid, b, a], "all", False, at_pc=True)
    assert out["added"] == [a, b] and [s["drama_id"] for s in out["skipped"]] == [999999, paid]
    assert all(s["reason"] for s in out["skipped"])
    assert [(i["drama_id"], i["step"]) for i in _items()] == [
        (d, s) for d in (a, b) for s in ("transcribe", "translate", "export")]
    assert out["queue"]["state"] == "running"


def test_bulk_add_one_step_and_a_bad_step(jobs):
    a = _drama("A")
    q.add_bulk(None, [a], "export", True, at_pc=True)
    assert [(i["drama_id"], i["step"]) for i in _items()] == [(a, "export")]
    with pytest.raises(InvalidInputError):
        q.add_bulk(None, [a], "dub", True, at_pc=True)


def test_bulk_add_skips_the_titles_that_no_longer_fit_when_the_queue_fills(jobs):
    did = _drama()
    for _ in range(q.MAX_ITEMS - 2):
        q.add_item(None, did, "export", True, at_pc=True)
    a = _drama("A")
    out = q.add_bulk(None, [a], "all", True, at_pc=True)
    assert out["added"] == [] and len(out["skipped"]) == 1
    assert len(_items()) == q.MAX_ITEMS - 2


def test_bulk_add_stops_checking_after_the_queue_is_full(jobs):
    did = _drama()
    for _ in range(q.MAX_ITEMS - 2):
        q.add_item(None, did, "export", True, at_pc=True)
    a, b = _drama("A"), _drama("B")
    out = q.add_bulk(None, [a, b], "all", True, at_pc=True)
    assert out["added"] == [] and [s["drama_id"] for s in out["skipped"]] == [a, b]
    assert out["skipped"][0]["reason"] == out["skipped"][1]["reason"]


def test_bulk_add_body_refuses_an_oversized_id_or_too_many_titles():
    from pydantic import ValidationError
    from api.schemas import QueueAddBulk
    with pytest.raises(ValidationError):
        QueueAddBulk(drama_ids=[1, 2**63], step="export")
    with pytest.raises(ValidationError):
        QueueAddBulk(drama_ids=list(range(1, q.MAX_ITEMS + 2)), step="export")
    assert QueueAddBulk(drama_ids=[1, 2], step="all").drama_ids == [1, 2]


def test_a_queued_transcribe_repeats_the_run_choices_saved_on_the_title(jobs):
    did = _drama()
    transcribe_service.update_transcribe_config(
        did, run_start={"run_diarize": True, "expected_speakers": 3, "extra_names": "白鹤"})
    q.add_item(None, did, "transcribe", True, at_pc=True)
    q.start(None)
    q.tick()
    assert jobs.transcribe_kwargs == [{"run_diarize": True, "expected_speakers": 3, "extra_names": "白鹤"}]
    jobs.finish(f"transcribe_{did}")


def test_a_failed_job_read_before_its_record_lands_keeps_no_path(isolated_db, monkeypatch):
    """The record still says running while the in-memory job already
    failed: the item's reason comes from the raw job error, so it gets the
    /api/jobs scrub (paths and URLs), not only key redaction."""
    from services import jobs_service
    monkeypatch.setattr(background_jobs, "get_status", lambda jid: {
        "status": "error", "error": "OSError: /home/someone/private/a.wav via http://10.0.0.5/x"})
    monkeypatch.setattr(jobs_service, "get_job", lambda jid: {"status": "running"})
    finished, status, reason = q._job_outcome("translate_1")
    assert finished and status == q.FAILED
    assert "someone/private" not in reason and "10.0.0.5" not in reason


def _paid_ready(cap, monkeypatch):
    real = settings_service.get
    # The source-match check defaults to running on this PC's GPU after the batches, which keeps the overlap off.
    overrides = {"translate_alignment_check": False}
    monkeypatch.setattr(settings_service, "get",
                        lambda k, *a, **kw: overrides[k] if k in overrides else real(k, *a, **kw))
    monkeypatch.setattr(translate_run_service.translate_service, "resolve_api_key", lambda name: "k")
    cap(5.0)


def _overlap_queue(translate_engine, on):
    a = _drama("A", engine=translate_engine, lines=(("你好", ""),))
    b = _drama("B")
    q.add_item(None, a, "translate", True, at_pc=True)
    q.add_item(None, b, "transcribe", True, at_pc=True)
    q.set_policy(None, overlap_online_translate=on)
    q.start(None)
    return a, b


def test_transcription_starts_beside_an_online_translation_when_switched_on(jobs, cap, monkeypatch):
    _paid_ready(cap, monkeypatch)
    a, b = _overlap_queue("claude", True)
    q.tick()
    q.tick()
    assert jobs.started == [f"translate_{a}", f"transcribe_{b}"]
    assert [i["status"] for i in _items()] == ["running", "running"]
    jobs.finish(f"translate_{a}")
    jobs.finish(f"transcribe_{b}")
    q.tick()
    assert [i["status"] for i in _items()] == ["done", "done"]


def test_the_source_match_check_no_longer_keeps_the_overlap_off(jobs, cap, monkeypatch):
    _paid_ready(cap, monkeypatch)
    real = settings_service.get
    monkeypatch.setattr(settings_service, "get",
                        lambda k, *a, **kw: True if k == "translate_alignment_check" else real(k, *a, **kw))
    monkeypatch.setattr(settings_service, "source_match_model_present", lambda: True)
    a, b = _overlap_queue("claude", True)
    q.tick()
    q.tick()
    assert jobs.started == [f"translate_{a}", f"transcribe_{b}"]


def test_overlap_is_off_by_default(jobs, cap, monkeypatch):
    _paid_ready(cap, monkeypatch)
    a, _b = _overlap_queue("claude", False)
    q.tick()
    q.tick()
    assert jobs.started == [f"translate_{a}"]




def test_the_same_title_never_overlaps_itself(jobs, cap, monkeypatch):
    _paid_ready(cap, monkeypatch)
    a = _drama("A", engine="claude")
    q.add_item(None, a, "translate", True, at_pc=True)
    q.add_item(None, a, "transcribe", True, at_pc=True)
    q.set_policy(None, overlap_online_translate=True)
    q.start(None)
    q.tick()
    q.tick()
    assert jobs.started == [f"translate_{a}"]


def test_a_policy_call_changes_only_what_it_sends(jobs):
    q.set_policy(None, stop_on_failure=True)
    q.set_policy(None, overlap_online_translate=True)
    state = q.get_queue(None)
    assert state["stop_on_failure"] and state["overlap_online_translate"]
    q.set_policy(None, stop_on_failure=False)
    assert q.get_queue(None)["overlap_online_translate"] is True


def test_adding_an_item_starts_the_idle_queue_by_itself(jobs):
    a = _drama("A")
    q.add_item(None, a, "transcribe", True, at_pc=True)
    q.tick()
    assert jobs.started == [f"transcribe_{a}"]


def test_adding_never_overrides_a_pause_a_start_time_or_a_failure_stop(jobs):
    a = _drama("A")
    q.set_schedule(None, "23:30", now=1_900_000_000.0)
    q.add_item(None, a, "transcribe", True, at_pc=True)
    assert q.get_queue(None)["state"] == "idle"
    q.set_schedule(None, None)
    q.start(None)
    q.pause(None)
    q.add_item(None, a, "export", True, at_pc=True)
    assert q.get_queue(None)["state"] == "paused"
    with q._lock:
        doc = q._load()
        doc["state"], doc["notice"] = q.IDLE, "Stopped because an item failed."
        q._save(doc)
    q.add_item(None, a, "export", True, at_pc=True)
    assert q.get_queue(None)["state"] == "idle"
    q.tick()
    assert jobs.started == []


def test_any_admin_may_control_the_queue_but_a_member_may_not(jobs):
    admin = {"user_id": 1, "is_admin": True, "permissions": ["jobs.start"]}
    member = {"user_id": 2, "is_admin": False, "permissions": ["jobs.start"]}
    assert q.get_queue(admin)["can_control"] is True
    q.pause(admin)
    q.set_schedule(admin, "23:30", now=1_900_000_000.0)
    q.start(admin)
    with pytest.raises(ForbiddenError):
        q.pause(member)


def test_a_member_pauses_resumes_and_cancels_their_own_waiting_item(jobs):
    u = auth_service.add_user("m@example.com")
    mine = _drama("Mine", owner_user_id=u["id"], is_private=1)
    other = _drama("Other")
    member = {"user_id": u["id"], "is_admin": False, "permissions": ["jobs.start"]}
    q.pause(None)
    q.start(None)
    q.add_item(None, other, "transcribe", True, at_pc=True)     # first in line, running at the next tick
    q.tick()
    q.add_item(member, mine, "transcribe", False, at_pc=True)
    item = [i for i in q.get_queue(member)["items"]][0]
    q.hold_item(member, item["id"], True)
    assert q.get_queue(member)["items"][0]["held"] is True
    jobs.finish(f"transcribe_{other}")
    q.tick()
    assert jobs.started == [f"transcribe_{other}"]              # the paused item is skipped
    q.hold_item(member, item["id"], False)
    q.tick()
    assert jobs.started == [f"transcribe_{other}", f"transcribe_{mine}"]
    q.cancel_item(member, item["id"])
    assert background_jobs.get_status(f"transcribe_{mine}")["status"] in ("cancelled", "running")
    jobs.finish(f"transcribe_{mine}")


def test_cancelling_a_waiting_item_marks_it_cancelled_and_only_its_adder_may(jobs):
    u = auth_service.add_user("m@example.com")
    v = auth_service.add_user("n@example.com")
    mine = _drama("Mine", owner_user_id=u["id"], is_private=1)
    member = {"user_id": u["id"], "is_admin": False, "permissions": ["jobs.start"]}
    stranger = {"user_id": v["id"], "is_admin": False, "permissions": ["jobs.start"]}
    q.pause(None)
    q.add_item(member, mine, "transcribe", False, at_pc=True)
    item_id = q.get_queue(member)["items"][0]["id"]
    with pytest.raises(NotFoundError):
        q.cancel_item(stranger, item_id)
    q.cancel_item(member, item_id)
    assert _status() == [("transcribe", "cancelled")]
    with pytest.raises(ConflictError):
        q.cancel_item(member, item_id)


def test_a_members_add_never_starts_another_accounts_waiting_items(jobs):
    u = auth_service.add_user("m@example.com")
    mine = _drama("Mine", owner_user_id=u["id"], is_private=1)
    owners = _drama("Owners")
    member = {"user_id": u["id"], "is_admin": False, "permissions": ["jobs.start"]}
    q.pause(None)
    q.add_item(None, owners, "transcribe", True, at_pc=True)
    with q._lock:
        doc = q._load()
        doc["state"] = q.IDLE
        q._save(doc)
    q.add_item(member, mine, "transcribe", False, at_pc=True)
    q.tick()
    assert q.get_queue(None)["state"] == "idle" and jobs.started == []
