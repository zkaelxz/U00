"""services/series_auto_service.py: the series auto-pipeline over the job queue, with the stage
start functions faked as in test_queue_service.py."""
import threading

import pytest

import db
from core import Line
from engine_backends import local
from jobs import job_launch, job_runner
from lib.errors import ConflictError, InvalidInputError, UnsupportedOperationError
from services import (auth_service, export_service, ownership_service, queue_service as q,
                      series_auto_service as auto, title_copy_service, transcribe_service,
                      translate_run_service)

WAIT = 10


class Fake:
    def __init__(self, monkeypatch):
        self.started, self.translate_kwargs, self.fail = [], [], set()
        self.gates = {}
        monkeypatch.setattr(transcribe_service, "start_transcribe_run",
                            lambda did, **kw: self._start(f"transcribe_{did}"))
        monkeypatch.setattr(translate_run_service, "start_translate_run", self._translate)
        monkeypatch.setattr(local, "check_ollama_reachable", lambda url: False)

    def _translate(self, did, **kw):
        self.translate_kwargs.append(kw)
        return self._start(f"translate_{did}")

    def _start(self, job_id):
        gate = self.gates.setdefault(job_id, threading.Event())
        fail = job_id in self.fail

        def body():
            if not gate.wait(WAIT):
                raise RuntimeError("test gate never opened")
            if fail:
                raise RuntimeError("engine said no")
        assert job_launch.start_job(job_id, body, description="fake", gpu_touching=False)
        self.started.append(job_id)
        return {"job_id": job_id}

    def finish(self, job_id):
        self.gates.setdefault(job_id, threading.Event()).set()
        assert job_runner.wait_for_job_threads(WAIT, job_ids=[job_id])


@pytest.fixture
def jobs(isolated_db, monkeypatch):
    fake = Fake(monkeypatch)
    yield fake
    for gate in list(fake.gates.values()):
        gate.set()
    job_runner.wait_for_job_threads(WAIT)


def _series(n=3, engine="ollama"):
    sid = db.get_or_create_series("Show")
    ids = [db.create_drama(title_en=f"Ep {i}", title_zh="题", translation_engine=engine, series_id=sid,
                           episode_number=i, audio_filename=f"{i}.mp3", transcript_mode="whisper")
           for i in range(1, n + 1)]
    return sid, ids


def _run_episode(jobs, did, spoken=True):
    """Drives the queue through the four items of one episode, as the stages would."""
    q.start(None)
    for step in ("transcribe", "translate"):
        q.tick()
        jobs.finish(f"{step}_{did}")
        if step == "transcribe":
            db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
        else:
            db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello")])
        q.tick()
    q.tick()    # export runs inline


def _on(sid, ceiling=1.0):
    return auto.switch_on(None, sid, ceiling, True)


def _auto_steps():
    return [(i["drama_id"], i["step"], i["status"]) for i in q.get_queue(None)["items"]]


def test_it_queues_one_episode_then_stops_for_review_until_continue(jobs):
    sid, (e1, e2, e3) = _series()
    _on(sid)
    auto.scan()
    assert [(d, s) for d, s, _ in _auto_steps()] == [(e1, "transcribe"), (e1, "translate"), (e1, "export")]
    auto.scan()
    assert len(_auto_steps()) == 3          # one episode at a time

    _run_episode(jobs, e1)
    assert [st for *_, st in _auto_steps()] == ["done"] * 3
    auto.scan()
    state = auto.get_state(None, sid)
    assert state["hold_kind"] == "review" and "Ep 1" in state["hold"]
    auto.scan()
    assert len(_auto_steps()) == 3          # nothing for episode 2 while it waits

    auto.resume(None, sid)
    auto.scan()
    assert {d for d, *_ in _auto_steps()} == {e1, e2}
    assert auto.get_state(None, sid)["reviewed"] is True


def test_auto_qc_runs_before_the_export_of_an_auto_item(jobs, monkeypatch):
    sid, (e1, *_rest) = _series(1)
    calls = []
    real = export_service.run_auto_qc_flagging
    monkeypatch.setattr(export_service, "run_auto_qc_flagging", lambda did: calls.append(did) or real(did))
    _on(sid)
    auto.scan()
    _run_episode(jobs, e1)
    assert calls == [e1]


def test_a_stopped_episode_holds_the_series_and_continue_skips_it(jobs):
    sid, (e1, e2, _e3) = _series()
    _on(sid)
    auto.scan()
    jobs.fail.add(f"transcribe_{e1}")
    q.start(None)
    q.tick()
    jobs.finish(f"transcribe_{e1}")
    q.tick()
    while any(st == "waiting" for *_, st in _auto_steps()):
        q.tick()
    auto.scan()
    state = auto.get_state(None, sid)
    assert state["hold_kind"] == "stopped" and "transcribe" in state["hold"]
    auto.scan()
    assert {d for d, *_ in _auto_steps()} == {e1}

    auto.resume(None, sid)
    auto.scan()
    assert e2 in {d for d, *_ in _auto_steps()}


def test_the_ceiling_holds_the_series_and_a_higher_one_releases_it(jobs, monkeypatch):
    sid, (e1, e2, _e3) = _series()
    _on(sid, ceiling=1.0)
    auto.scan()
    _run_episode(jobs, e1)
    auto.scan()
    auto.resume(None, sid)
    monkeypatch.setattr(db, "get_spend_for_dramas", lambda ids: 1.0)
    auto.scan()
    state = auto.get_state(None, sid)
    assert state["hold_kind"] == "ceiling" and state["left_usd"] == 0
    assert e2 not in {d for d, *_ in _auto_steps()}
    with pytest.raises(UnsupportedOperationError):
        auto.resume(None, sid)
    auto.set_ceiling(None, sid, 5.0)
    assert auto.get_state(None, sid)["hold"] == ""
    auto.scan()
    assert e2 in {d for d, *_ in _auto_steps()}


def test_a_paid_translate_runs_without_a_monthly_cap_under_the_series_ceiling(jobs, monkeypatch):
    monkeypatch.setattr(translate_run_service.translate_service, "resolve_api_key", lambda name: "k")
    sid, (e1, *_rest) = _series(1, engine="claude")
    db.save_lines(e1, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    _on(sid, ceiling=2.0)
    q._append_items(None, e1, [("translate", {})], True, auto_series=sid)
    q.start(None)
    q.tick()
    assert jobs.started == [f"translate_{e1}"]
    cap = jobs.translate_kwargs[0]["job_cost_cap_usd"]
    assert 0 < cap <= 2.0


def test_a_paid_translate_whose_estimate_exceeds_the_ceiling_is_skipped(jobs, monkeypatch):
    monkeypatch.setattr(translate_run_service.translate_service, "resolve_api_key", lambda name: "k")
    sid, (e1, *_rest) = _series(1, engine="claude")
    db.save_lines(e1, [Line(idx=i, start=i, end=i + 1, zh="长" * 400, en="") for i in range(50)])
    _on(sid, ceiling=0.01)
    q._append_items(None, e1, [("translate", {})], True, auto_series=sid)
    q.start(None)
    q.tick()
    item = q.get_queue(None)["items"][0]
    assert item["status"] == "skipped" and "spending ceiling" in item["reason"]
    assert jobs.started == []


def test_a_used_up_ceiling_is_never_a_zero_cap(jobs, monkeypatch):
    sid, _ids = _series(1)
    _on(sid, ceiling=1.0)
    monkeypatch.setattr(db, "get_spend_for_dramas", lambda ids: 1.0)
    with pytest.raises(UnsupportedOperationError):
        auto.ceiling_left(sid)


def test_switching_off_withdraws_waiting_items_and_blocks_a_late_start(jobs):
    sid, (e1, *_rest) = _series()
    _on(sid)
    auto.scan()
    auto.switch_off(None, sid)
    assert q.get_queue(None)["items"] == []
    auto.scan()
    assert q.get_queue(None)["items"] == []
    with pytest.raises(UnsupportedOperationError):
        auto.ceiling_left(sid)


def test_a_title_without_media_or_with_lines_is_not_queued(jobs):
    sid, (e1, e2, e3) = _series()
    db.update_drama(e1, audio_filename=None)
    db.save_lines(e2, [Line(idx=0, start=0, end=1, zh="x", en="y")])
    _on(sid)
    auto.scan()
    assert {d for d, *_ in _auto_steps()} == {e3}


def test_a_test_copy_is_never_queued(jobs):
    sid, (e1, e2, e3) = _series()
    db.update_drama(e1, custom_tags="Hot, test COPY")
    db.update_drama(e2, custom_tags=title_copy_service.COPY_TAG)
    assert auto._next_episode(sid, auto._read(sid))["id"] == e3
    _on(sid)
    auto.scan()
    assert {d for d, *_ in _auto_steps()} == {e3}


def test_a_tag_that_only_contains_the_copy_tag_is_still_queued(jobs):
    sid, (e1, _e2, _e3) = _series()
    db.update_drama(e1, custom_tags="Test copy 2, Hot")
    _on(sid)
    auto.scan()
    assert {d for d, *_ in _auto_steps()} == {e1}


def test_inputs_and_state_rules():
    for bad in (0, -1, True, "5", 5000):
        with pytest.raises(InvalidInputError):
            auto._check_ceiling(bad)


def test_cannot_switch_on_twice_or_continue_when_not_waiting(jobs):
    sid, _ids = _series(1)
    _on(sid)
    with pytest.raises(ConflictError):
        _on(sid)
    with pytest.raises(ConflictError):
        auto.resume(None, sid)


# --- "Waiting for you" when a hold starts -----------------------------------------

@pytest.fixture
def told(monkeypatch):
    from services import notification_service
    calls = []
    monkeypatch.setattr(notification_service, "notify_waiting",
                        lambda text, **kw: calls.append({"text": text, **kw}))
    return calls


def _by(sid, user_id):
    state = auto._read(sid)
    state["by"]["user_id"] = user_id
    auto._write(sid, state)


def test_the_review_hold_is_announced_once_to_whoever_switched_it_on(jobs, told):
    sid, (e1, *_rest) = _series()
    _on(sid)
    auto.scan()
    _run_episode(jobs, e1)
    assert told == []
    _by(sid, 7)     # set after the episode was queued, which needs a real account
    auto.scan()
    auto.scan()
    auto.scan()
    text = "Ep 1 is done. Check its subtitles; press Continue to run the rest."
    assert told == [{"text": text, "push_text": text, "owner_user_id": 7,
                     "target": {"page": "review", "drama_id": e1}, "names_drama_id": e1}]


def test_a_private_title_is_not_named_in_the_push(jobs, told):
    sid, (e1, *_rest) = _series()
    db.update_drama(e1, is_private=1, owner_user_id=5)
    _on(sid)
    auto.scan()
    _run_episode(jobs, e1)
    auto.scan()
    (call,) = told
    assert call["text"].startswith("Ep 1 is done.")
    assert call["push_text"] == "An episode is done. Check its subtitles; press Continue to run the rest."


def test_a_stopped_episode_is_announced_once(jobs, told):
    sid, (e1, *_rest) = _series()
    _on(sid)
    auto.scan()
    jobs.fail.add(f"transcribe_{e1}")
    q.start(None)
    q.tick()
    jobs.finish(f"transcribe_{e1}")
    q.tick()
    while any(st == "waiting" for *_, st in _auto_steps()):
        q.tick()
    auto.scan()
    auto.scan()
    (call,) = told
    assert call["text"].startswith("Ep 1 stopped at transcribe:") and "engine said no" in call["text"]
    # The push is built from fixed parts: the reason stays in the bell.
    assert call["push_text"] == ("Ep 1 stopped at transcribe. Retry it on the Jobs page, "
                                 "or press Continue to skip it.")
    assert call["target"] == {"page": "series", "series_id": sid}
    assert call["text"] == auto.get_state(None, sid)["hold"]


def test_the_ceiling_hold_names_the_series_in_the_push_only_when_it_is_shared(jobs, told, monkeypatch):
    monkeypatch.setattr(db, "get_spend_for_dramas", lambda ids: 1.0)
    sid, _ids = _series(1)
    _on(sid, ceiling=1.0)
    auto.scan()
    auto.scan()
    hidden = db.get_or_create_series("Hidden", owner_user_id=5, is_private=True)
    db.create_drama(title_en="H1", series_id=hidden, translation_engine="ollama", episode_number=1,
                    audio_filename="h.mp3", transcript_mode="whisper")
    _on(hidden, ceiling=1.0)
    auto.scan()
    held = "Spending ceiling reached ($1.00 of $1.00)."
    assert [(c["text"], c["push_text"], c["target"]) for c in told] == [
        (f"{held} - Show", f"{held} - Show", {"page": "series", "series_id": sid}),
        (f"{held} - Hidden", held, {"page": "series", "series_id": hidden})]


def test_an_episode_that_cannot_be_queued_is_announced_once(jobs, told, monkeypatch):
    sid, (e1, *_rest) = _series()
    db.update_drama(e1, is_private=1, owner_user_id=5)

    def refuse(*a, **k):
        raise UnsupportedOperationError("No key for this engine.")
    monkeypatch.setattr(q, "add_auto_episode", refuse)
    _on(sid)
    auto.scan()
    auto.scan()
    (call,) = told
    assert call["text"] == "Ep 1 could not be queued: No key for this engine."
    assert call["push_text"] == "An episode could not be queued. Open the series page to see why."
    assert call["target"] == {"page": "series", "series_id": sid}
    assert call["names_drama_id"] == e1


def test_a_hold_after_continue_is_announced_again(jobs, told, monkeypatch):
    sid, (e1, *_rest) = _series()
    _on(sid)
    auto.scan()
    _run_episode(jobs, e1)
    auto.scan()
    auto.resume(None, sid)
    monkeypatch.setattr(db, "get_spend_for_dramas", lambda ids: 5.0)
    auto.scan()
    auto.scan()
    assert [c["text"][:20] for c in told] == ["Ep 1 is done. Check ", "Spending ceiling rea"]


def test_a_failing_announcement_never_breaks_the_hold(jobs, monkeypatch):
    from services import notification_service

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(notification_service, "household_sees", boom)
    sid, (e1, *_rest) = _series()
    _on(sid)
    auto.scan()
    _run_episode(jobs, e1)
    auto.scan()
    assert auto.get_state(None, sid)["hold_kind"] == "review"


def _fail_first_episode(jobs, sid, e1, reason=None):
    """Runs episode 1's transcribe into a failure; `reason` replaces the item's error text."""
    auto.scan()
    jobs.fail.add(f"transcribe_{e1}")
    q.start(None)
    q.tick()
    jobs.finish(f"transcribe_{e1}")
    q.tick()
    while any(st == "waiting" for *_, st in _auto_steps()):
        q.tick()
    if reason is not None:
        with q._lock:
            doc = q._load()
            for item in doc["items"]:
                if item["status"] == "failed":
                    item["reason"] = reason
            q._save(doc)


@pytest.fixture
def pushes(monkeypatch):
    """The real notify_waiting with a channel set: (bell entries, pushed texts)."""
    from services import notification_service as ns
    monkeypatch.setenv("BAIHE_DISCORD_WEBHOOK_URL",
                       "https://discord.com/api/webhooks/123456789012345678/"
                       "SECRETtokenABCDEFGHIJKLMNOPQRSTUVWXYZ_-0123")
    monkeypatch.delenv(ns.DISABLED_ENV, raising=False)
    monkeypatch.setattr(ns, "_schedule_flush", lambda: type("T", (), {"cancel": lambda self: None})())
    ns.reset_for_tests()
    yield (lambda: [e["text"] for e in ns.list_recent(None) if e["kind"] == "waiting"],
           lambda: [m for st, m, _c in ns._pending if st == "waiting"])
    ns.reset_for_tests()


LEAKY = ("FileNotFoundError: /home/mei/Baihe/dramas/12/Ep 1 01.mp3; "
         "HTTPConnectionPool(host='192.168.1.20', port=11434): Max retries exceeded with url: /api/chat")


@pytest.mark.parametrize("private", [False, True])
def test_a_stopped_episode_push_never_quotes_its_error(jobs, pushes, private):
    bell, pushed = pushes
    sid, (e1, *_rest) = _series()
    if private:
        db.update_drama(e1, is_private=1, owner_user_id=5)
    _on(sid)
    _fail_first_episode(jobs, sid, e1, LEAKY)
    auto.scan()
    shown = "An episode" if private else "Ep 1"
    assert pushed() == [f"Waiting for you: {shown} stopped at transcribe. Retry it on the Jobs page, "
                        "or press Continue to skip it."]
    for bit in ("01.mp3", "/home", "192.168.1.20", "11434", "/api/chat", "HTTPConnectionPool"):
        assert bit not in pushed()[0]
    (entry,) = bell()
    assert entry.startswith("Waiting for you: Ep 1 stopped at transcribe: ")
    assert "192.168.1.20" in entry and "01.mp3" in entry     # the bell keeps the reason


@pytest.mark.parametrize("private", [False, True])
def test_removed_queue_items_hold_push_sentence(jobs, told, private):
    sid, (e1, *_rest) = _series()
    if private:
        db.update_drama(e1, is_private=1, owner_user_id=5)
    _on(sid)
    auto.scan()
    q.remove_waiting_auto_items(sid)
    auto.scan()
    (call,) = told
    assert call["text"] == "Ep 1's queue items were removed before they finished."
    shown = "An episode" if private else "Ep 1"
    assert call["push_text"] == f"{shown}'s queue items were removed before they finished."
    assert call["names_drama_id"] == e1


def test_an_episode_that_cannot_be_queued_names_a_shared_title_but_not_the_error(jobs, told,
                                                                                 monkeypatch):
    sid, (_e1, *_rest) = _series()

    def refuse(*a, **k):
        raise UnsupportedOperationError("No key at http://192.168.1.20:11434 for Ep 1 01.mp3.")
    monkeypatch.setattr(q, "add_auto_episode", refuse)
    _on(sid)
    auto.scan()
    (call,) = told
    assert call["text"].startswith("Ep 1 could not be queued: No key at")
    assert call["push_text"] == "Ep 1 could not be queued. Open the series page to see why."


def test_a_stopped_private_episode_push_says_an_episode(jobs, told):
    sid, (e1, *_rest) = _series()
    db.update_drama(e1, is_private=1, owner_user_id=5)
    _on(sid)
    _fail_first_episode(jobs, sid, e1)
    auto.scan()
    (call,) = told
    assert call["push_text"] == ("An episode stopped at transcribe. Retry it on the Jobs page, "
                                 "or press Continue to skip it.")


def test_a_hold_entry_follows_what_its_owner_can_still_see(jobs):
    """A hold's entry has no job id, so the bell checks the title or series it points at."""
    from services import notification_service as ns
    ns.reset_for_tests()
    member = auth_service.add_user("kid@example.com")
    kid = {"user_id": member["id"], "is_admin": False, "is_local_owner": False}
    admin = {"user_id": 999, "is_admin": True, "is_local_owner": False}
    sid, (e1, *_rest) = _series()
    try:
        ns.notify_waiting("Spending ceiling reached.", push_text="x", owner_user_id=member["id"],
                          target={"page": "series", "series_id": sid})
        ns.notify_waiting("Ep 1 is done.", push_text="x", owner_user_id=member["id"],
                          target={"page": "review", "drama_id": e1})
        ns.notify_waiting("The queue stopped.", push_text="x", owner_user_id=member["id"],
                          target={"page": "jobs"})

        def texts(p):
            return [e["text"] for e in ns.list_recent(p) if e["kind"] == "waiting"]
        everything = ["Waiting for you: The queue stopped.", "Waiting for you: Ep 1 is done.",
                      "Waiting for you: Spending ceiling reached."]
        assert texts(kid) == everything
        db.update_drama(e1, is_private=1, owner_user_id=5)
        assert texts(kid) == everything[:1] + everything[2:]
        _make_series_private(sid, 5)
        assert texts(kid) == everything[:1]
        assert texts(admin) == everything
        assert texts(None) == everything
    finally:
        ns.reset_for_tests()


@pytest.mark.parametrize("how", ["its own flag", "moved into a private series"])
def test_a_stopped_hold_entry_is_hidden_once_its_episode_goes_private(jobs, how):
    """The stopped hold's entry points at the series but names the episode and quotes its error.
    The app clears a title's own private flag while it is in a series, so today an episode goes
    out of a member's sight by moving into a series they cannot see; a row from before that rule
    can still carry its own flag."""
    from services import notification_service as ns
    ns.reset_for_tests()
    member = auth_service.add_user("kid@example.com")
    kid = {"user_id": member["id"], "is_admin": False, "is_local_owner": False}
    admin = {"user_id": 999, "is_admin": True, "is_local_owner": False}
    sid, (e1, *_rest) = _series()
    try:
        _on(sid)
        _fail_first_episode(jobs, sid, e1)
        _by(sid, member["id"])
        auto.scan()

        def texts(p):
            return [e["text"] for e in ns.list_recent(p) if e["kind"] == "waiting"]
        (entry,) = texts(kid)
        assert entry.startswith("Waiting for you: Ep 1 stopped at transcribe: ")
        if how == "its own flag":
            db.update_drama(e1, is_private=1, owner_user_id=5)
        else:
            elsewhere = db.get_or_create_series("Locked Shelf")
            _make_series_private(elsewhere, 5)
            db.update_drama(e1, series_id=elsewhere)
        assert ownership_service.can_see(kid, "series", sid)
        assert texts(kid) == []
        assert texts(admin) == [entry] and texts(None) == [entry]
    finally:
        ns.reset_for_tests()


def _make_series_private(series_id, owner):
    import contextlib
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE series SET is_private = 1, owner_user_id = ? WHERE id = ?",
                     (owner, series_id))
        conn.commit()

