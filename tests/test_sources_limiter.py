import threading
import time

from sources.limiter import Limiter


def test_stale_looser_client_cannot_run_beside_a_stricter_one():
    gate = Limiter()
    entered = threading.Event()

    with gate.at(1):
        def stale():
            with gate.at(2):
                entered.set()
        t = threading.Thread(target=stale)
        t.start()
        time.sleep(0.1)
        assert not entered.is_set()
    t.join(2)
    assert entered.is_set()


def test_strict_client_waits_for_a_looser_one_in_flight():
    gate = Limiter()
    with gate.at(2):
        with gate.at(2):
            assert gate.active == 2
    assert gate.active == 0
    with gate.at(2):
        entered = threading.Event()

        def careful():
            with gate.at(1):
                entered.set()
        t = threading.Thread(target=careful)
        t.start()
        time.sleep(0.1)
        assert not entered.is_set()
    t.join(2)
    assert entered.is_set()


def test_loose_clients_cannot_keep_a_strict_waiter_out():
    gate = Limiter()
    release = threading.Event()
    running = []

    def worker():
        with gate.at(4):
            running.append(1)
            release.wait(5)

    first = [threading.Thread(target=worker) for _ in range(3)]
    for t in first:
        t.start()
    time.sleep(0.1)
    assert gate.active == 3

    strict_in = threading.Event()

    def strict():
        with gate.at(1):
            strict_in.set()

    st = threading.Thread(target=strict)
    st.start()
    time.sleep(0.1)
    late = threading.Thread(target=worker)
    late.start()
    time.sleep(0.1)
    # The late loose client waits behind the strict one rather than joining the running three.
    assert gate.active == 3
    assert not strict_in.is_set()

    release.set()
    for t in first + [st, late]:
        t.join(3)
    assert strict_in.is_set()
    assert len(running) == 4
    assert gate.active == 0


def test_cancelled_waiter_drops_its_limit(monkeypatch):
    gate = Limiter()
    with gate.at(2):
        def boom(*a, **k):
            raise RuntimeError("cancelled")
        monkeypatch.setattr(gate._cond, "wait", boom)
        try:
            with gate.at(1):
                raise AssertionError("should not enter")
        except RuntimeError:
            pass
        monkeypatch.undo()
        assert gate._waiting == []
        with gate.at(2):
            assert gate.active == 2


def test_single_client_never_deadlocks():
    gate = Limiter()
    for _ in range(3):
        with gate.at(1):
            assert gate.active == 1
    assert gate.active == 0 and gate._waiting == []


def test_a_waiting_client_gives_up_when_its_cancel_check_raises():
    gate = Limiter()
    cancelled = threading.Event()
    outcome = []

    def check():
        if cancelled.is_set():
            raise RuntimeError("cancelled")

    def waiter():
        try:
            with gate.at(1, check):
                outcome.append("entered")
        except RuntimeError:
            outcome.append("cancelled")

    with gate.at(1):
        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.3)
        assert outcome == []
        cancelled.set()
        t.join(2)
    assert outcome == ["cancelled"]
    assert gate.active == 0
    with gate.at(1):   # the cancelled waiter left the queue clean
        pass


def test_a_blocking_wait_callback_does_not_stop_another_client_entering():
    gate = Limiter()
    in_callback = threading.Event()
    release = threading.Event()
    entered = threading.Event()

    def slow_check():
        in_callback.set()
        release.wait(5)

    def blocked_waiter():
        with gate.at(1, slow_check):
            pass

    def other():
        with gate.at(5):
            entered.set()

    holder = gate.at(1)
    holder.__enter__()
    t = threading.Thread(target=blocked_waiter)
    t.start()
    assert in_callback.wait(2)
    holder.__exit__(None, None, None)
    t2 = threading.Thread(target=other)
    t2.start()
    try:
        assert entered.wait(1), "a client was blocked behind another's wait callback"
    finally:
        release.set()
        t.join(2)
        t2.join(2)
