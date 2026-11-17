"""Races that only show when threads reach the check at the same moment: each test releases N threads
through a Barrier and asserts that exactly the allowed number win."""
import threading
from types import SimpleNamespace

import background_jobs as bg
from api import llm_slots
from jobs import gpu_slots, job_launch
from lib.errors import RateLimitedError

THREADS = 8


def _race(worker):
    barrier = threading.Barrier(THREADS)
    outcomes = []

    def go(n):
        barrier.wait()
        outcomes.append(worker(n))

    threads = [threading.Thread(target=go, args=(n,)) for n in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    assert len(outcomes) == THREADS
    return outcomes


def test_starting_the_same_job_id_at_once_starts_it_once(isolated_db):
    release = threading.Event()
    try:
        outcomes = _race(lambda _n: job_launch.start_job("same_id", release.wait, 10.0))
        assert outcomes.count(True) == 1
        assert outcomes.count(False) == THREADS - 1
    finally:
        release.set()
        bg.clear_all_jobs()


def test_one_gpu_slot_goes_to_one_taker(isolated_db):
    outcomes = _race(lambda n: gpu_slots.take(f"ui:holder{n}", "x", max_holders=1))
    assert outcomes.count(True) == 1
    assert gpu_slots.holder_count() == 1


def test_llm_slots_never_run_past_the_server_cap():
    # The winners hold their slots until every thread has tried for one: a
    # timer could free them before a slow runner gets the last threads there,
    # and those would then win too.
    release = threading.Event()
    attempts = []
    attempts_lock = threading.Lock()

    def tried():
        with attempts_lock:
            attempts.append(1)
            if len(attempts) == THREADS:
                release.set()

    def worker(n):
        request = SimpleNamespace(state=SimpleNamespace(principal={"user_id": n}))
        try:
            with llm_slots.llm_slot(request):
                tried()
                release.wait(timeout=5.0)
            return True
        except RateLimitedError:
            tried()
            return False

    outcomes = _race(worker)
    assert outcomes.count(True) == llm_slots.LLM_MAX_IN_FLIGHT
    assert llm_slots.ACTIVE_CALLERS == set()
