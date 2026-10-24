"""
lib/file_lock.py: a second holder waits until the first lets go, and the
lock is released (and its descriptor closed) when the body raises.
"""
import os
import threading
import time

import pytest

from lib import file_lock


def test_a_second_holder_waits_for_the_first(tmp_path):
    path = str(tmp_path / "x.lock")
    order = []
    entered = threading.Event()

    def second():
        entered.wait(5)
        with file_lock.hold(path, poll=0.01):
            order.append("second")

    t = threading.Thread(target=second)
    t.start()
    with file_lock.hold(path, poll=0.01):
        entered.set()
        time.sleep(0.15)
        order.append("first")
    t.join(5)
    assert not t.is_alive()
    assert order == ["first", "second"]


def test_the_lock_is_released_when_the_body_raises(tmp_path):
    path = str(tmp_path / "x.lock")
    with pytest.raises(RuntimeError):
        with file_lock.hold(path):
            raise RuntimeError("boom")
    fd = os.open(path, os.O_RDWR)
    try:
        assert file_lock._try_lock(fd) is True
    finally:
        os.close(fd)


def test_an_unopenable_lock_file_raises_instead_of_waiting(tmp_path):
    with pytest.raises(OSError):
        with file_lock.hold(str(tmp_path / "missing-dir" / "x.lock")):
            pass
