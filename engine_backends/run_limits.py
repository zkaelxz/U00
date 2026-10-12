"""Engine-independent limits that end a translate run early: too many failed
batches in a row, and too much time spent waiting out rate limits."""

import contextvars

CONSECUTIVE_FAILURE_LIMIT = 3
# A fully rate-limited batch waits about a minute, so this ends a run after
# roughly ten of them instead of hours.
RATE_LIMIT_WAIT_BUDGET_SECONDS = 600.0
RATE_LIMIT_STOP_MESSAGE = ("Stopped: the provider keeps rate-limiting you; try later or switch engine. "
                           "Lines already translated are kept.")


class TranslateRunStopped(RuntimeError):
    """Ends the whole run; stops_run is what shared.stops_translate_run reads."""
    stops_run = True


class RateLimitWaitBudget:
    def __init__(self, seconds: float = RATE_LIMIT_WAIT_BUDGET_SECONDS):
        self.remaining = seconds

    def charge(self, delay: float):
        if delay > self.remaining:
            # Spent for good: a later, shorter wait must not slip through and
            # let the run send another paid request.
            self.remaining = 0.0
            raise TranslateRunStopped(RATE_LIMIT_STOP_MESSAGE)
        self.remaining -= delay


# Set for the duration of a run, like shared's cancel check, because the
# backoff sleeps sit below every call the pipeline could pass a budget to.
_rate_limit_budget_var = contextvars.ContextVar("translate_rate_limit_budget", default=None)


def charge_rate_limit_wait(delay: float):
    """Called before each rate-limit sleep; raises once the run's budget is spent."""
    budget = _rate_limit_budget_var.get()
    if budget is not None:
        budget.charge(delay)


class FailureStreak:
    """Counts batches in a row that failed outright. A batch that was
    cancelled or only blocked by a content filter says nothing about the
    engine, so it neither adds to the streak nor resets it."""

    def __init__(self, limit: int = CONSECUTIVE_FAILURE_LIMIT):
        self.limit = limit
        self.count = 0

    def record(self, *, failed: bool, succeeded: bool) -> bool:
        """Notes one finished batch; True when the streak reached the limit."""
        if succeeded:
            self.count = 0
        elif failed:
            self.count += 1
        return self.count >= self.limit

    def stop_message(self, last_error: str) -> str:
        return f"Stopped: {self.count} batches in a row failed ({last_error}). Lines already translated are kept."
