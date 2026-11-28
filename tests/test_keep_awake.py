"""lib/keep_awake.py: Windows is asked to stay awake only while there is work, from one thread."""
import threading
import time

from lib import keep_awake as ka

WAIT = 5.0
HOLD = ka.ES_CONTINUOUS | ka.ES_SYSTEM_REQUIRED


class FakeWindows:
    def __init__(self):
        self.calls = []
        self.threads = set()
        self.changed = threading.Event()

    def __call__(self, flags):
        self.calls.append(flags)
        self.threads.add(threading.get_ident())
        self.changed.set()
        return 1

    def wait_for(self, n):
        deadline = time.monotonic() + WAIT
        while len(self.calls) < n and time.monotonic() < deadline:
            time.sleep(0.01)
        return len(self.calls) >= n


def _run(busy, fake=None):
    fake = fake or FakeWindows()
    keeper = ka.KeepAwake(busy, set_state=fake, interval=0.01)
    assert keeper.start()
    return keeper, fake


def test_held_while_busy_and_released_when_the_last_job_ends():
    work = {"jobs": 1}
    keeper, fake = _run(lambda: work["jobs"] > 0)
    try:
        assert fake.wait_for(1) and fake.calls[0] == HOLD
        assert keeper.holding
        work["jobs"] = 0
        assert fake.wait_for(2) and fake.calls[1] == ka.ES_CONTINUOUS
    finally:
        keeper.stop()
    assert fake.calls == [HOLD, ka.ES_CONTINUOUS]   # never the display flag, never repeated


def test_idle_never_asks_windows_for_anything():
    keeper, fake = _run(lambda: False)
    time.sleep(0.1)
    keeper.stop()
    assert fake.calls == []


def test_stopping_while_busy_releases_on_the_same_thread_that_set_it():
    keeper, fake = _run(lambda: True)
    assert fake.wait_for(1)
    keeper.stop()
    assert fake.calls == [HOLD, ka.ES_CONTINUOUS]
    assert len(fake.threads) == 1 and threading.get_ident() not in fake.threads


def test_a_failing_check_counts_as_idle():
    state = {"boom": False}

    def busy():
        if state["boom"]:
            raise RuntimeError("job table unreadable")
        return True
    keeper, fake = _run(busy)
    try:
        assert fake.wait_for(1)
        state["boom"] = True
        assert fake.wait_for(2) and fake.calls[-1] == ka.ES_CONTINUOUS
    finally:
        keeper.stop()


def test_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(ka.os, "name", "posix")
    keeper = ka.KeepAwake(lambda: True)
    assert keeper.start() is False
    keeper.stop()


def test_the_server_holds_it_while_a_job_runs(isolated_db, monkeypatch):
    import threading as th
    from dataclasses import replace
    from api import background
    from api.api_config import ApiSettings
    from jobs import job_launch, job_runner
    fake = FakeWindows()
    monkeypatch.setattr(ka, "_windows_setter", lambda: fake)
    monkeypatch.setattr(ka, "CHECK_SECONDS", 0.01)
    assert background.start_keep_awake(ApiSettings()) is False   # background services off
    gate = th.Event()
    try:
        assert background.start_keep_awake(replace(ApiSettings(), background_services=True))
        time.sleep(0.1)
        assert fake.calls == []   # idle: nothing asked
        assert job_launch.start_job("keep_awake_test", lambda: gate.wait(WAIT), description="test")
        assert fake.wait_for(1) and fake.calls[0] == HOLD
        gate.set()
        assert job_runner.wait_for_job_threads(WAIT, job_ids=["keep_awake_test"])
        assert fake.wait_for(2) and fake.calls[1] == ka.ES_CONTINUOUS
    finally:
        gate.set()
        background.stop_keep_awake()


def test_taking_and_releasing_the_hold_is_logged(caplog):
    import logging
    import time
    from lib.keep_awake import KeepAwake
    busy = [True]
    keeper = KeepAwake(lambda: busy[0], set_state=lambda flags: None, interval=0.01)
    with caplog.at_level(logging.INFO, logger="baihe"):
        keeper.start()
        deadline = time.monotonic() + 2
        while not keeper.holding and time.monotonic() < deadline:
            time.sleep(0.01)
        busy[0] = False
        while keeper.holding and time.monotonic() < deadline:
            time.sleep(0.01)
        keeper.stop()
    messages = [r.getMessage() for r in caplog.records]
    assert "Keep PC awake: on (a job is queued or running)" in messages
    assert "Keep PC awake: off (no jobs left)" in messages
