"""The fair lock Quick read pages queue on, so the earliest page goes first."""

import page_turns


class TestFairLock:
    def test_lowest_priority_waiter_gets_the_lock_first(self):
        import threading
        import time
        lock = page_turns.FairLock()
        order = []
        lock.acquire()
        threads = []
        for page in (5, 2, 9, 1):
            def run(page=page):
                lock.acquire(priority=(0, page))
                order.append(page)
                lock.release()
            t = threading.Thread(target=run)
            t.start()
            threads.append(t)
            time.sleep(0.05)
        lock.release()
        for t in threads:
            t.join(5)
        assert order == [1, 2, 5, 9]

    def test_without_priority_waiters_go_in_arrival_order(self):
        import threading
        import time
        lock = page_turns.FairLock()
        order = []
        lock.acquire()
        threads = []
        for n in range(4):
            def run(n=n):
                with lock:
                    order.append(n)
            t = threading.Thread(target=run)
            t.start()
            threads.append(t)
            time.sleep(0.05)
        lock.release()
        for t in threads:
            t.join(5)
        assert order == [0, 1, 2, 3]

    def test_locked_and_page_priority(self):
        lock = page_turns.FairLock()
        assert not lock.locked()
        with lock:
            assert lock.locked()
        assert page_turns.page_priority("p25") == (0, 25)
        assert page_turns.page_priority("") == (0, 0)


    def test_acquire_gives_up_after_its_timeout_without_blocking_others(self):
        lock = page_turns.FairLock()
        lock.acquire()
        assert lock.acquire(timeout=0.05) is False
        assert lock.acquire(blocking=False) is False
        lock.release()
        assert lock.acquire(timeout=0.05) is True
        lock.release()


    def test_quick_read_pages_go_ahead_of_callers_with_no_priority(self):
        import threading
        import time
        lock = page_turns.FairLock()
        order = []
        lock.acquire()

        def plain():
            with lock:
                order.append("plain")

        def quick():
            lock.acquire(priority=page_turns.page_priority("p3"))
            order.append("quick")
            lock.release()

        threads = [threading.Thread(target=plain), threading.Thread(target=quick)]
        for t in threads:
            t.start()
            time.sleep(0.05)
        lock.release()
        for t in threads:
            t.join(5)
        assert order == ["quick", "plain"]
