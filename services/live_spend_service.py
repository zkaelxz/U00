"""Money limits for a Live session whose translator is a paid or hosted engine.

Live translates a chunk at a time for up to hours, unattended once started, so
each request re-checks this month's cap, is logged to the usage log whether or
not it worked, and a run of failed requests ends the session instead of billing
or failing forever."""
import applog
import background_jobs
import translate_engines
from engine_backends import paid_calls
from engine_backends.local import scrub_error_text
from engine_backends.run_limits import FailureStreak
from services import translate_run_service
from lib.errors import UnsupportedOperationError

USAGE_OPERATION = "live_translate"
CAP_LABEL = "Stopped: monthly cap reached"


class LiveSpendGuard:
    """stop(reason, label) ends the session; it is called once. reason is the
    full sentence for the session notes, label the short one for the status line."""

    def __init__(self, engine, engine_name: str, stop):
        self._engine, self._name, self._stop = engine, engine_name, stop
        self._streak = FailureStreak()
        self._halted = False
        # A hosted Ollama model sends the text off this PC, so it is logged
        # even though it has no per-token price.
        model = getattr(engine, "model", "") or ""
        self._metered = (engine_name not in translate_engines.FREE_ENGINES
                         or translate_engines.is_ollama_cloud_model(model))

    def _halt(self, reason: str, label: str) -> None:
        if not self._halted:
            self._halted = True
            self._stop(reason, label)

    def before(self) -> None:
        """Called before each translation request. Raises JobCancelled once the
        session has been stopped, here or earlier."""
        if not self._halted and translate_run_service.engine_cap_applies(
                self._name, bool(getattr(self._engine, "free_tier", False))):
            try:
                translate_run_service.refuse_when_cap_spent(
                    self._name, bool(getattr(self._engine, "free_tier", False)))
            except UnsupportedOperationError as refusal:
                self._halt(f"Live stopped: {refusal}", CAP_LABEL)
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
        if error is None and not usable:
            return
        if error is None:
            self._streak.record(failed=False, succeeded=True)
        elif self._streak.record(failed=True, succeeded=False):
            self._halt(f"Live stopped: {self._streak.count} translation requests in a row failed "
                       f"({scrub_error_text(str(error))}). The cues already shown are kept.",
                       f"Stopped: {self._streak.count} translation requests failed in a row")

    def _log(self) -> None:
        try:
            paid_calls.record_call(self._engine, self._name, USAGE_OPERATION,
                                   getattr(self._engine, "last_usage", None), metered=True)
        except Exception as exc:
            # Losing a usage row must not take the captions down with it.
            applog.get_logger().warning(f"live usage not logged: {scrub_error_text(str(exc))}")
