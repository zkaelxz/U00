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
