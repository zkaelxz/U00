"""A clock a test moves by hand, in place of sleeping past a one-second timestamp resolution."""
import datetime


class FrozenClock:
    def __init__(self, start: datetime.datetime):
        self.now = start

    def __call__(self) -> datetime.datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += datetime.timedelta(seconds=seconds)


def frozen_clock(monkeypatch, *modules) -> FrozenClock:
    """Freezes `_now` in each module at the current time and returns the clock to advance."""
    clock = FrozenClock(datetime.datetime.now(datetime.timezone.utc))
    for mod in modules:
        monkeypatch.setattr(mod, "_now", clock)
    return clock
