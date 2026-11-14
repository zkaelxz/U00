"""services/series_auto_service.py: the series auto-pipeline over the job queue, with the stage
start functions faked as in test_queue_service.py."""
import threading

import pytest

import db
from core import Line
from engine_backends import local
from jobs import job_launch, job_runner
from lib.errors import ConflictError, InvalidInputError, UnsupportedOperationError
from services import (export_service, queue_service as q, series_auto_service as auto,
                      title_copy_service, transcribe_service, translate_run_service)

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
