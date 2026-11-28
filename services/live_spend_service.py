"""Money limits for a Live session whose translator is a paid or hosted engine.

Live translates a chunk at a time for up to hours, unattended once started, so
each request re-checks this month's cap, is logged to the usage log whether or
not it worked, and a run of failed requests ends the session instead of billing
or failing forever."""
import threading

import applog
import background_jobs
from engine_backends import engine_registry
from engine_backends import paid_calls, pricing
from engine_backends.local import scrub_error_text
from engine_backends.run_limits import FailureStreak
from services import translate_run_service
from lib.errors import UnsupportedOperationError

USAGE_OPERATION = "live_translate"
CAP_LABEL = "Stopped: monthly cap reached"
SESSION_LIMIT_LABEL = "Stopped: this session's spend limit was reached"
DEFAULT_SESSION_LIMIT_USD = 2.0
SESSION_LIMIT_RANGE = (0.1, 100.0)


class SessionBudget:
    """The spend limit of one Live session that runs on a paid engine without a
    monthly cap: without one, a session left running for hours has no ceiling.
    The cue and chat translators share it, so it is the session's total."""

    def __init__(self, limit_usd: float):
        self.limit_usd = float(limit_usd)
        self._spent = 0.0
        self._lock = threading.Lock()

    def add(self, usd: float) -> None:
        with self._lock:
            self._spent += max(0.0, float(usd or 0.0))

    def reached(self) -> bool:
        with self._lock:
            return self._spent >= self.limit_usd

    def spent_usd(self) -> float:
        with self._lock:
            return self._spent


def session_budget(engine, engine_name: str, limit_usd: float):
    """A SessionBudget when the engine is billed and no monthly cap is set, else
    None: a monthly cap already bounds every session, and a free engine costs nothing."""
    if translate_run_service.month_cap_usd() or not translate_run_service.engine_cap_applies(
            engine_name, bool(getattr(engine, "free_tier", False)), getattr(engine, "model", None)):
        return None
    return SessionBudget(limit_usd)


class LiveSpendGuard:
    """stop(reason, label) ends the session; it is called once. reason is the
    full sentence for the session notes, label the short one for the status line."""

    def __init__(self, engine, engine_name: str, stop, budget: "SessionBudget | None" = None):
        self._engine, self._name, self._stop = engine, engine_name, stop
        self._budget = budget
        self._streak = FailureStreak()
        self._halted = False
        self._metered = engine_name not in engine_registry.FREE_ENGINES

    @property
    def halted(self) -> bool:
        return self._halted

    def session_spend(self):
        """(spent_usd, limit_usd) of the session budget, or None without one."""
        if self._budget is None:
            return None
        return self._budget.spent_usd(), self._budget.limit_usd

    def _halt(self, reason: str, label: str) -> None:
        if not self._halted:
            self._halted = True
            self._stop(reason, label)

    def before(self) -> None:
        """Called before each translation request. Raises JobCancelled once the
        session has been stopped, here or earlier."""
        if not self._halted and translate_run_service.engine_cap_applies(
                self._name, bool(getattr(self._engine, "free_tier", False)),
                getattr(self._engine, "model", None)):
            try:
                translate_run_service.refuse_when_cap_spent(
                    self._name, bool(getattr(self._engine, "free_tier", False)),
                    getattr(self._engine, "model", None))
            except UnsupportedOperationError as refusal:
                self._halt(f"Live stopped: {refusal}", CAP_LABEL)
        if not self._halted and self._budget is not None and self._budget.reached():
            self._halt(f"Live stopped: this session's translations reached its spend limit of "
                       f"${self._budget.limit_usd:.2f} (set on the Live screen; a monthly cap in "
                       "Settings replaces it). The cues already shown are kept.", SESSION_LIMIT_LABEL)
        if self._halted:
            raise background_jobs.JobCancelled("live translate")
        # Engines set last_usage only on success; a failed request must not
        # log the previous request's tokens again.
        if isinstance(getattr(self._engine, "last_usage", None), dict):
            self._engine.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def after(self, error=None, usable: bool = True) -> None:
        """Called after each translation request that was not cancelled. A reply
        that came back but cannot be used is logged (it was billed) and counts
        neither as a success nor as a failure."""
        if self._metered:
            self._log()
        if self._budget is not None:
            self._budget.add(self._cost())
        if error is None and not usable:
            return
        if error is None:
            self._streak.record(failed=False, succeeded=True)
        elif self._streak.record(failed=True, succeeded=False):
            self._halt(f"Live stopped: {self._streak.count} translation requests in a row failed "
                       f"({scrub_error_text(str(error))}). The cues already shown are kept.",
                       f"Stopped: {self._streak.count} translation requests failed in a row")

    def _cost(self) -> float:
        usage = getattr(self._engine, "last_usage", None)
        if not isinstance(usage, dict):
            return 0.0
        try:
            return pricing.estimate_cost_for_engine(self._engine, *(
                usage.get(k, 0) or 0 for k in ("input_tokens", "output_tokens",
                                               "cache_read_tokens", "cache_write_tokens")))
        except Exception:
            return 0.0

    def _log(self) -> None:
        try:
            paid_calls.record_call(self._engine, self._name, USAGE_OPERATION,
                                   getattr(self._engine, "last_usage", None), metered=True)
        except Exception as exc:
            # Losing a usage row must not take the captions down with it.
            applog.get_logger().warning(f"live usage not logged: {scrub_error_text(str(exc))}")
