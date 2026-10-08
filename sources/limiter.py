"""sources/limiter.py -- the per-source concurrency limit."""

import threading


class Limiter:
    """A concurrency limit that can be re-tuned while requests are in flight.
    Clients built before and after a pace change ask for different limits; a
    lower limit applies at once and a higher one only once the source is idle,
    so alternating clients cannot swap their way past the stricter one."""

    def __init__(self, limit: int):
        self.limit = limit
        self.active = 0
        self._cond = threading.Condition()

    def set_limit(self, limit: int):
        with self._cond:
            self.limit = min(self.limit, limit) if self.active else limit
            self._cond.notify_all()

    def __enter__(self):
        with self._cond:
            while self.active >= self.limit:
                self._cond.wait()
            self.active += 1
        return self

    def __exit__(self, *exc):
        with self._cond:
            self.active -= 1
            self._cond.notify_all()
