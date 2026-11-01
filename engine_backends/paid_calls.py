"""Usage rows and run limits for AI calls that are not a translation batch.

record_call writes a usage row from a call's reported usage dict; usage_logger
writes one from the (tokens in, tokens out) callbacks of the LLM helpers. CallLimits
is opened around a job's call_llm_json calls: it re-checks the monthly cap
before each one, ends the job after a run of failed requests, shares one
rate-limit wait budget, and can write each call's usage row itself."""

import contextlib
import contextvars
import threading

from .engine_registry import FREE_ENGINES
from .local import is_ollama_cloud_model, scrub_error_text
from .pricing import estimate_cost_for_engine
from .run_limits import (RATE_LIMIT_STOP_MESSAGE, RATE_LIMIT_STOP_REASON, FailureStreak,
                         RateLimitWaitBudget, TranslateRunStopped, _rate_limit_budget_var)
from .shared import ContentModerationBlocked, LLMTaskTimeout, TranslationCancelled, stops_translate_run

CAP_STOP_REASON = "Stopped: monthly cap reached"


def failure_stop_reason(count: int) -> str:
    return f"Stopped: {count} requests failed in a row"


def stop_reason_for(exc) -> str:
    """The plain sentence for an error that ends a whole run, else ''."""
    if isinstance(exc, TranslateRunStopped):
        # The translate wording promises kept lines, which a job may not have.
        return RATE_LIMIT_STOP_REASON if str(exc) == RATE_LIMIT_STOP_MESSAGE else str(exc)
    if stops_translate_run(exc):
        return f"Stopped: {scrub_error_text(str(exc))}"
    return ""


def is_paid(engine_name: str, engine=None) -> bool:
    """A hosted Ollama model sends the text off this PC, so it is logged even
    though it has no per-token price."""
    return engine_name not in FREE_ENGINES or is_ollama_cloud_model(getattr(engine, "model", ""))


def record_call(engine, engine_name: str, operation: str, usage: dict | None = None, *,
                drama_id: int | None = None, metered: bool | None = None) -> bool:
    """Writes one usage row; False when nothing was written. A paid engine is
    always logged, a request that failed included (the provider may have
    billed it, with zero tokens when none were reported); a free engine only
    when it reported tokens. `metered` overrides the paid test for a caller
    with its own (the translate meter's cap rule)."""
    usage = usage or {}
    inp, out, cache_read, cache_write = (usage.get(k, 0) or 0 for k in
                                         ("input_tokens", "output_tokens",
                                          "cache_read_tokens", "cache_write_tokens"))
    if not ((is_paid(engine_name, engine) if metered is None else metered) or inp or out):
        return False
    import db
    db.log_usage(drama_id, engine_name, getattr(engine, "model", None) or engine_name, operation,
                 inp, out, estimate_cost_for_engine(engine, inp, out, cache_read, cache_write),
                 cache_read_tokens=cache_read)
    return True


def usage_logger(drama_id, engine_name: str, engine, operation: str):
    """A (tokens in, tokens out) callback that logs one usage row per call,
    for the LLM helpers that report only token counts. The cache-aware
    variants that also take cache token counts stay with their callers."""
    def log(inp, out, *_):
        import db
        db.log_usage(drama_id, engine_name, getattr(engine, "model", None) or engine_name,
                     operation, inp, out, estimate_cost_for_engine(engine, inp, out))
    return log


# Groq bills hosted Whisper by the hour of audio, not by token.
GROQ_USD_PER_AUDIO_HOUR = 0.04


def record_audio_call(engine_name: str, model: str, operation: str, seconds: float, failed: bool,
                      *, drama_id: int | None = None) -> None:
    """A hosted speech-to-text request. A failed one is logged too, at no cost:
    nothing it returned was billed as far as is known."""
    import db
    cost = 0.0 if failed else seconds / 3600 * GROQ_USD_PER_AUDIO_HOUR
    db.log_usage(drama_id, engine_name, model, operation, 0, 0, cost)


class _Call:
    def __init__(self):
        self.usage = {}
        self.logged = False

    def capture(self, inp=0, out=0, cache_read=0, cache_write=0):
        self.usage = {"input_tokens": inp, "output_tokens": out,
                      "cache_read_tokens": cache_read, "cache_write_tokens": cache_write}


class CallLimits:
    """cap_spent() is true once this month's cap is used up; None for an
    engine the cap does not cover. stop_reason is the plain sentence for the
    job's result once a rule has ended the job."""

    def __init__(self, engine, engine_name: str, operation: str, *, drama_id: int | None = None,
                 cap_spent=None, log_usage: bool = False):
        self._engine, self._operation = engine, operation
        self._name = engine_name or getattr(engine, "name", None) or type(engine).__name__
        self._drama_id, self._cap_spent, self._log_usage = drama_id, cap_spent, log_usage
        self._streak = FailureStreak()
        self._lock = threading.Lock()
        self.stop_reason = None

    def _stop(self, reason: str) -> None:
        with self._lock:
            self.stop_reason = self.stop_reason or reason

    def _begin(self) -> _Call:
        if self.stop_reason is None and self._cap_spent is not None and self._cap_spent():
            self._stop(CAP_STOP_REASON)
        if self.stop_reason is not None:
            raise TranslateRunStopped(self.stop_reason)
        return _Call()

    def _write(self, call: _Call) -> None:
        with self._lock:
            if call.logged:
                return
            call.logged = True
        try:
            record_call(self._engine, self._name, self._operation, call.usage,
                        drama_id=self._drama_id)
        except Exception as exc:
            import applog
            # Losing a usage row must not take the job's result down with it.
            applog.get_logger().warning(f"usage not logged: {scrub_error_text(str(exc))}")

    def call(self, run, usage_cb, logs: bool = True):
        """run(usage_cb, on_late_result) makes the request. A caller that has
        its own usage_cb keeps logging itself; this logs only when it has none.
        logs=False for a request whose usage does not arrive through usage_cb."""
        call = self._begin()
        logs = logs and self._log_usage and usage_cb is None
        try:
            result = run(call.capture if logs else usage_cb,
                         (lambda: self._write(call)) if logs else None)
        except BaseException as exc:
            self._failed(call, exc, logs)
            raise
        self._streak.record(failed=False, succeeded=True)
        if logs:
            self._write(call)
        return result

    def _failed(self, call: _Call, exc: BaseException, logs: bool) -> None:
        # A timed-out or cancelled request is still running, and writes its
        # own row with real tokens when it returns (on_late_result).
        if logs and not isinstance(exc, (LLMTaskTimeout, TranslationCancelled)):
            self._write(call)
        reason = stop_reason_for(exc)
        if reason:
            self._stop(reason)
        elif isinstance(exc, (TranslationCancelled, ContentModerationBlocked)):
            return  # says nothing about the engine
        elif self._streak.record(failed=True, succeeded=False):
            self._stop(failure_stop_reason(self._streak.count))


_limits_var: contextvars.ContextVar["CallLimits | None"] = contextvars.ContextVar("paid_call_limits", default=None)


def current_limits():
    return _limits_var.get()


@contextlib.contextmanager
def call_limits(engine, engine_name, operation: str, *, drama_id: int | None = None,
                cap_spent=None, log_usage: bool = False):
    """Applies CallLimits to every call_llm_json inside the block, and gives
    them one rate-limit wait budget."""
    limits = CallLimits(engine, engine_name, operation, drama_id=drama_id,
                        cap_spent=cap_spent, log_usage=log_usage)
    tokens = (_limits_var.set(limits), _rate_limit_budget_var.set(RateLimitWaitBudget()))
    try:
        yield limits
    finally:
        _rate_limit_budget_var.reset(tokens[1])
        _limits_var.reset(tokens[0])


@contextlib.contextmanager
def logged_calls(engine, operation: str, *, drama_id: int | None = None):
    """Writes a usage row for each call_llm_json in the block, failed ones
    included. Inside a job that already has its limits, those apply instead."""
    if current_limits() is not None:
        yield current_limits()
        return
    with call_limits(engine, None, operation, drama_id=drama_id, log_usage=True) as limits:
        yield limits

