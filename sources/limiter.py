"""sources/limiter.py -- the per-source concurrency limit."""

import threading


class Limiter:
    """Concurrency gate shared by every client of one source. Each client
    enters with the limit of its own pace policy, and may only enter while
    the requests already running and the clients still waiting all allow one
    more, so a stale client built before a pace change cannot run beside a
    stricter one in flight. Counting waiters stops looser clients re-entering
    forever and starving a stricter one. Idle, nothing is held back: the next
    client's own limit applies."""

    def __init__(self):
        self._held = []
        self._waiting = []
        self._cond = threading.Condition()

    @property
    def active(self) -> int:
        return len(self._held)

    def at(self, limit: int):
        return _Entry(self, max(1, int(limit)))


class _Entry:
    def __init__(self, gate: Limiter, limit: int):
        self._gate = gate
        self._limit = limit

    def __enter__(self):
        g = self._gate
        with g._cond:
            g._waiting.append(self._limit)
            try:
                while len(g._held) >= min([*g._held, *g._waiting]):
                    g._cond.wait()
            finally:
                g._waiting.remove(self._limit)
                # A departing strict waiter may be what was holding others back.
                g._cond.notify_all()
            g._held.append(self._limit)
        return self

    def __exit__(self, *exc):
        g = self._gate
        with g._cond:
            g._held.remove(self._limit)
            g._cond.notify_all()
