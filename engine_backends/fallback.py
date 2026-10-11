"""Translate fallback chain."""

from lib.http import FetchError

from .engine_registry import ENGINES, TRANSLATION_ONLY_ENGINES, unknown_engine_message
from .pricing import estimate_cost_for_engine
from .shared import (
    ContentModerationBlocked,
    _cancellable_sleep,
    _empty_usage,
    ProviderRedirected,
    _is_rate_limit_error,
    redact_secrets,
)


# ---------------------------------------------------------------------------
# Translate fallback chain
# ---------------------------------------------------------------------------

# Matched against exception CLASS NAMES (whole MRO) so this module needn't
# import every provider SDK (anthropic, openai, requests...), some optional,
# just to recognise their timeout/connection/auth errors.
_FALLBACK_NAME_HINTS = ("timeout", "connectionerror", "apiconnection", "authentication",
                        "permissiondenied", "unauthorized")


def is_fallback_error(e: Exception) -> bool:
    """True only for a real transient/credential failure worth trying the
    next engine for: auth failure (401/403), rate limit, timeout, or
    connection error. Never a content-moderation refusal (handled
    separately) and never a bare/unknown exception, which could be a real
    bug rather than a real provider problem."""
    if isinstance(e, ContentModerationBlocked):
        return False
    status = getattr(e, "status_code", None)
    resp = getattr(e, "response", None)
    if resp is not None:
        status = status or getattr(resp, "status_code", None)
    if status in (401, 403) or _is_rate_limit_error(e):
        return True
    return isinstance(e, FetchError) or any(
        hint in cls.__name__.lower()
        for cls in type(e).__mro__ for hint in _FALLBACK_NAME_HINTS)


# Transient errors (rate limit, timeout, connection)
# retry the SAME engine with a short capped backoff before the chain moves
# on; auth errors still switch immediately (waiting cannot fix a bad key).
FALLBACK_TRANSIENT_RETRIES = 2
FALLBACK_BACKOFF_BASE_SECONDS = 1.0
FALLBACK_BACKOFF_CAP_SECONDS = 8.0
_fallback_sleep = _cancellable_sleep  # patchable so tests never really sleep
_TRANSIENT_NAME_HINTS = ("timeout", "connectionerror", "apiconnection")


def is_transient_fallback_error(e: Exception) -> bool:
    """True for the is_fallback_error cases where retrying the same engine
    can help: rate limit, timeout, connection error. Never auth (401/403)."""
    if not is_fallback_error(e):
        return False
    status = getattr(e, "status_code", None)
    resp = getattr(e, "response", None)
    if resp is not None:
        status = status or getattr(resp, "status_code", None)
    if status in (401, 403):
        return False
    if _is_rate_limit_error(e):
        return True
    if isinstance(e, ProviderRedirected):  # repeats identically; switch engines instead
        return False
    return isinstance(e, FetchError) or any(
        hint in cls.__name__.lower()
        for cls in type(e).__mro__ for hint in _TRANSIENT_NAME_HINTS)


MAX_FALLBACK_ENGINES = 2


def fallback_chain_error(names):
    """Why an ordered engine chain [primary, *fallbacks] can't run, or None.
    Shared by the translate run service (API/React) and `cli.py translate
    --fallback`: at most MAX_FALLBACK_ENGINES fallbacks, no engine twice, known engines only, and never mixing
    instruction-following engines with TRANSLATION_ONLY_ENGINES."""
    names = list(names)
    if len(names) > MAX_FALLBACK_ENGINES + 1:
        return f"A fallback chain takes at most {MAX_FALLBACK_ENGINES} fallback engines."
    if len(set(names)) != len(names):
        return "A fallback chain can't repeat an engine."
    for n in names:
        if n not in ENGINES:
            return unknown_engine_message(n).replace("Unknown engine.", "Unknown translate engine.")
    if len(names) > 1:
        if len({n in TRANSLATION_ONLY_ENGINES for n in names}) > 1:
            return ("A fallback chain can't mix instruction-following engines with "
                    "translation-only ones.")
    return None


class FallbackEngine:
    """Wraps an ordered chain of engines of the SAME class (all
    instruction-following, or all in TRANSLATION_ONLY_ENGINES -- the caller
    enforces that, so glossary/style adherence is never silently dropped).
    translate_batch tries the active engine; on a transient error it first
    retries that engine up to FALLBACK_TRANSIENT_RETRIES times with a capped
    backoff, and only then (or at once for an auth error) on an
    is_fallback_error it switches -- for the rest of the run -- to the next one, recording the
    switch in `events`. Everything else (name/model/free_tier/last_usage/
    supports_reference...) reads through to the active engine so cost and
    usage logging stay correct per engine. Each engine has its own cost
    cap and its own spend. A finished batch always counts against the
    engine that ran it; so does a failed attempt whose provider reported
    tokens before the error (its last_usage -- e.g. a parse retry that was
    billed, then a rate limit): that spend is added to the failing engine's
    `spent` and passed to `failed_usage_cb(choice, engine, input_tokens,
    output_tokens, cache_read_tokens, cache_write_tokens)` when set, so the
    caller can log it. An attempt that reports no tokens adds nothing.
    """

    def __init__(self, engines: list, choices: list, caps: list = None,
                 failed_usage_cb=None):
        self.engines = list(engines)
        self.choices = list(choices)
        self.caps = list(caps) if caps else [None] * len(engines)
        self.spent = [0.0] * len(engines)
        self.active = 0
        self.events = []
        self.failed_usage_cb = failed_usage_cb

    def __getattr__(self, name):
        # Only reached when normal lookup fails. Dunders (copy/pickle probes)
        # must not resolve to the wrapped engine's, and engines/active are
        # unset on an instance built without __init__ (copy, unpickle):
        # delegating those would recurse back into __getattr__ forever.
        if name.startswith("__") or name in ("engines", "active"):
            raise AttributeError(name)
        return getattr(self.engines[self.active], name)

    @property
    def active_choice(self) -> str:
        return self.choices[self.active]

    def cap_exhausted(self) -> bool:
        cap = self.caps[self.active]
        return cap is not None and self.spent[self.active] >= cap

    def translate_batch(self, zh_lines, context):
        retries = 0
        while True:
            engine = self.engines[self.active]
            if isinstance(getattr(engine, "last_usage", None), dict):
                # A failed attempt must not re-count the previous batch's usage.
                engine.last_usage = _empty_usage()
            try:
                result = engine.translate_batch(zh_lines, context)
            except Exception as e:
                self._record_failed_usage(engine)
                if not is_fallback_error(e):
                    raise
                if is_transient_fallback_error(e) and retries < FALLBACK_TRANSIENT_RETRIES:
                    _fallback_sleep(min(FALLBACK_BACKOFF_CAP_SECONDS,
                                        FALLBACK_BACKOFF_BASE_SECONDS * (2 ** retries)))
                    retries += 1
                    continue
                if self.active + 1 >= len(self.engines):
                    # Tells shared.call_with_backoff the whole chain already
                    # retried this rate limit, so it doesn't wait it out again.
                    # Guarded: some exception types refuse new attributes.
                    try:
                        e._fallback_chain_exhausted = True
                    except Exception:
                        pass
                    raise
                retries = 0
                self.events.append({"from": self.choices[self.active],
                                    "to": self.choices[self.active + 1],
                                    "reason": type(e).__name__,
                                    "detail": redact_secrets(str(e))[:200]})
                self.active += 1
                continue
            u = getattr(engine, "last_usage", None)
            if u:
                self.spent[self.active] += estimate_cost_for_engine(
                    engine, u.get("input_tokens", 0), u.get("output_tokens", 0),
                    u.get("cache_read_tokens", 0), u.get("cache_write_tokens", 0))
            return result

    def _record_failed_usage(self, engine):
        """A failed attempt's reported tokens count against the engine that
        spent them (see the class docstring)."""
        u = getattr(engine, "last_usage", None) or {}
        tokens = [u.get(k, 0) or 0 for k in ("input_tokens", "output_tokens",
                                             "cache_read_tokens", "cache_write_tokens")]
        if not any(tokens):
            return
        self.spent[self.active] += estimate_cost_for_engine(engine, *tokens)
        if self.failed_usage_cb:
            self.failed_usage_cb(self.choices[self.active], engine, *tokens)
