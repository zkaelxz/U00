"""Hold a lock while staying cancellable during the wait for it."""
import contextlib

POLL_SECONDS = 0.2


@contextlib.contextmanager
def hold(lock, cancel_check=None, poll: float = POLL_SECONDS):
    """Holds `lock`, calling `cancel_check` between acquire attempts. The check
    raises to give up, so a job stuck behind a long model run can still be
    cancelled; without one this is a plain blocking acquire."""
    if cancel_check is None:
        lock.acquire()
    else:
        while not lock.acquire(timeout=poll):
            cancel_check()
    try:
        yield
    finally:
        lock.release()
