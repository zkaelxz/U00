"""
services/engine_failure_service.py -- a fixed sentence and a reason for an
AI engine call that failed, for the features that ask an engine one
question (metadata autofill, romanize credits, Discover lookups) and used
to answer every failure with one generic "unavailable".

The sentence names what the user can act on: a used-up daily quota, a
refused key, a rate limit, Ollama not running, a safety block, a slow or
unreachable engine. It never carries the provider's own text, a URL or a
key; the real cause goes to the server log, redacted.
"""
import logging

import requests

import translate_engines
from engine_backends.shared import LLMTaskTimeout
from lib import http
from lib.errors import DependencyUnavailableError

log = logging.getLogger(__name__)

_LABELS = {"claude": "Claude", "gemini": "Gemini", "deepseek": "DeepSeek",
           "openai": "OpenAI", "ollama": "Ollama"}


def _status(exc) -> int:
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else 0


def _reason(exc) -> tuple:
    """(reason, sentence with {name} for the engine), or (None, None)."""
    if isinstance(exc, translate_engines.FreeTierDailyLimitReached):
        return "daily_limit", ("{name}'s daily request limit for this model is used up. "
                               "Try again tomorrow, or pick another engine.")
    if isinstance(exc, translate_engines.ContentModerationBlocked):
        return "blocked", "{name} withheld its answer (a safety filter)."
    # SDK errors are matched by class name so no SDK import is needed here;
    # openai's and anthropic's APITimeoutError subclass APIConnectionError.
    sdk = {cls.__name__ for cls in type(exc).__mro__}
    if (isinstance(exc, (LLMTaskTimeout, requests.ReadTimeout, http.ResponseTooSlow, TimeoutError))
            or "APITimeoutError" in sdk):
        return "timed_out", "{name} took too long to answer. Try again."
    status = _status(exc)
    if status in (401, 403):
        return "key_rejected", "{name} refused the key. Check the {name} key in Settings."
    if status == 429:
        return "rate_limited", "{name}'s rate limit was reached. Try again later."
    if status >= 500:
        return "unavailable", "{name} is unavailable right now. Try again later."
    if isinstance(exc, (http.FetchError, requests.ConnectionError)) or "APIConnectionError" in sdk:
        return "unreachable", "Could not reach {name}. Check the connection and try again."
    return None, None


def engine_failure(exc: BaseException, engine_name: str, fallback: str) -> DependencyUnavailableError:
    """The error to raise for `exc`, a failed call to `engine_name`;
    `fallback` (fixed text) when the cause is not one the user can act on."""
    if isinstance(exc, translate_engines.OllamaUnavailableError):
        return DependencyUnavailableError(translate_engines.redact_secrets(exc.message),
                                          details={"reason": exc.reason})
    name = _LABELS.get(engine_name, "The AI engine")
    reason, sentence = _reason(exc)
    log.warning("%s call failed (%s): %s", name, reason or "failed",
                translate_engines.redact_secrets(f"{type(exc).__name__}: {exc}")[:300])
    if reason is None:
        return DependencyUnavailableError(fallback, details={"reason": "failed", "engine": name})
    return DependencyUnavailableError(sentence.format(name=name),
                                      details={"reason": reason, "engine": name})
