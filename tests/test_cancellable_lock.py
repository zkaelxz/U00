import threading
import time

import pytest

from lib import cancellable_lock


class Cancelled(Exception):
    pass


def test_cancel_while_the_lock_is_held_ends_the_wait():
    lock = threading.Lock()
    cancelled = threading.Event()
    outcome = []

    def check():
        if cancelled.is_set():
            raise Cancelled

    def waiter():
        try:
            with cancellable_lock.hold(lock, check):
                outcome.append("acquired")
        except Cancelled:
            outcome.append("cancelled")

    with lock:
        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.3)
        assert t.is_alive()
        cancelled.set()
        started = time.monotonic()
        t.join(2)
        assert not t.is_alive() and time.monotonic() - started < 1
    assert outcome == ["cancelled"]
    assert not lock.locked()


def test_the_lock_is_held_inside_and_released_after_even_on_error():
    lock = threading.Lock()
    with pytest.raises(ValueError):
        with cancellable_lock.hold(lock, lambda: None):
            assert lock.locked()
            raise ValueError
    assert not lock.locked()


def test_without_a_cancel_check_it_waits_for_the_lock():
    lock = threading.Lock()
    lock.acquire()
    threading.Timer(0.3, lock.release).start()
    started = time.monotonic()
    with cancellable_lock.hold(lock):
        waited = time.monotonic() - started
        assert lock.locked()
    assert waited >= 0.25
