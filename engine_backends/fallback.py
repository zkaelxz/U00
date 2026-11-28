"""Translate fallback chain."""

import contextvars
import threading
import time
from typing import Any

from lib.http import FetchError

from .engine_registry import ENGINES, TRANSLATION_ONLY_ENGINES, unknown_engine_message
from .pricing import estimate_cost_for_engine
from .shared import (
    ContentModerationBlocked,
    FreeTierDailyLimitReached,
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


def _is_server_outage(status) -> bool:
    """5xx other than 501 (not implemented, which repeats identically)."""
    return isinstance(status, int) and 500 <= status < 600 and status != 501


def is_fallback_error(e: Exception) -> bool:
    """True only for a real transient/credential failure worth trying the
    next engine for: auth failure (401/403), no credit left (402), rate limit, timeout, or
    connection error. Never a content-moderation refusal (handled
    separately) and never a bare/unknown exception, which could be a real
    bug rather than a real provider problem."""
    if isinstance(e, ContentModerationBlocked):
        return False
    if isinstance(e, FreeTierDailyLimitReached):  # the next engine has its own quota
        return True
    status = getattr(e, "status_code", None)
    resp = getattr(e, "response", None)
    if resp is not None:
        status = status or getattr(resp, "status_code", None)
    if status in (401, 402, 403) or _is_rate_limit_error(e) or _is_server_outage(status):
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
    can help: rate limit, timeout, connection error. Never auth or credit (401/402/403)."""
    if not is_fallback_error(e):
        return False
    status = getattr(e, "status_code", None)
    resp = getattr(e, "response", None)
    if resp is not None:
        status = status or getattr(resp, "status_code", None)
    if status in (401, 402, 403):
        return False
    if _is_rate_limit_error(e) or _is_server_outage(status):
        return True
    if isinstance(e, ProviderRedirected):  # repeats identically; switch engines instead
        return False
    return isinstance(e, FetchError) or any(
        hint in cls.__name__.lower()
        for cls in type(e).__mro__ for hint in _TRANSIENT_NAME_HINTS)


MAX_FALLBACK_ENGINES = 2


def fallback_chain_error(names):
    """Why an ordered engine chain [primary, *fallbacks] can't run, or None.
    Shared by the translate run service: at most MAX_FALLBACK_ENGINES fallbacks, no engine twice, known engines only, and never mixing
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

    def __init__(self, engines: list, choices: list, caps: list | None = None,
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
                        setattr(e, "_fallback_chain_exhausted", True)
                    except Exception:
                        pass
                    raise
                retries = 0
                self.events.append({"from": self.choices[self.active],
                                    "to": self.choices[self.active + 1],
                                    "to_model": getattr(self.engines[self.active + 1], "model", None),
                                    "reason": OUT_OF_CREDIT if is_out_of_credit(e) else type(e).__name__,
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


# The owner's line for "too slow" is 10 s for a page (about 15 lines), so a request is slow
# past that, or past the same pace per line for a longer batch.
SLOW_REQUEST_SECONDS = 10.0
SLOW_SECONDS_PER_LINE = SLOW_REQUEST_SECONDS / 15
SLOW_REQUESTS_TO_SWITCH = 2
# A provider that was slow stays slow for a while, so the next runs start on the fallback
# rather than paying for two more slow requests first.
SLOW_MEMORY_SECONDS = 30 * 60
_slow_until = {}
_slow_reason = {}
_slow_until_lock = threading.Lock()
_clock = time.monotonic  # patchable


def is_slow_request(seconds: float, n_lines: int) -> bool:
    return seconds > max(SLOW_REQUEST_SECONDS, n_lines * SLOW_SECONDS_PER_LINE)


def _cannot_serve(e: Exception) -> bool:
    """The fallback isn't there at all (no server, refused sign-in), as opposed to a busy
    moment (rate limit, outage, timeout) that the pipeline's own retries wait out."""
    if type(e).__name__ == "OllamaUnavailableError":
        return getattr(e, "reason", None) != "ollama_timeout"
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403):
        return True
    names = [cls.__name__.lower() for cls in type(e).__mro__]
    return (any("connectionerror" in n or "apiconnection" in n for n in names)
            and not any("timeout" in n for n in names))


def _engine_key(engine):
    return (getattr(engine, "name", ""), getattr(engine, "model", ""))


def recently_slow(engine) -> bool:
    with _slow_until_lock:
        return _clock() < _slow_until.get(_engine_key(engine), 0.0)


def _remember_slow(engine, reason: str = "slow"):
    with _slow_until_lock:
        _slow_until[_engine_key(engine)] = _clock() + SLOW_MEMORY_SECONDS
        _slow_reason[_engine_key(engine)] = reason


def _earlier_reason(engine) -> str:
    with _slow_until_lock:
        return _slow_reason.get(_engine_key(engine), "slow")


# DeepSeek answers 402 "Insufficient Balance" once its prepaid credit is used up; every
# later request fails the same way until the owner tops up, so the run moves at once.
OUT_OF_CREDIT = "out_of_credit"
OUT_OF_CREDIT_NOTICE = ("DeepSeek is out of credit. Top up at platform.deepseek.com; "
                        "Gemma is translating for now.")


_TOP_UP_PAGES = {"deepseek": "platform.deepseek.com"}
_ENGINE_LABELS = {"deepseek": "DeepSeek", "claude": "Claude", "gemini": "Gemini", "openai": "OpenAI",
                  "ollama": "Ollama"}


def out_of_credit_notice(from_engine: str, to_engine: str, to_model=None,
                         handed_back: bool = False) -> str:
    """The finished job's sentence for a switch made because `from_engine`'s account ran dry.
    `to_model` None means the automatic slow switch, whose fallback is always Gemma; a chosen
    chain names its model, since an Ollama fallback there may be any model. handed_back: the
    fallback couldn't serve and the run went back, so nothing is translating in its place."""
    name = _ENGINE_LABELS.get(from_engine, from_engine)
    page = _TOP_UP_PAGES.get(from_engine)
    top_up = f"Top up at {page}" if page else "Top up that account"
    if handed_back:
        return f"{name} is out of credit. {top_up}."
    gemma = to_engine == "ollama" and (to_model is None or str(to_model).startswith("gemma"))
    if from_engine == "deepseek" and gemma:
        return OUT_OF_CREDIT_NOTICE
    to = "Gemma" if gemma else _ENGINE_LABELS.get(to_engine, to_engine)
    if to_engine == "ollama" and not gemma:
        to = f"Ollama ({to_model})"
    return f"{name} is out of credit. {top_up}; {to} is translating for now."


def is_out_of_credit(e: Exception) -> bool:
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    return status == 402


class _SlowSwitchState:
    """Shared by a run's lane copies: whether the run has moved off the slow engine."""

    def __init__(self, switched: bool):
        self.lock = threading.Lock()
        self.switched = switched
        self.fallback_failed = False
        self.slow_in_a_row = 0
        self.events = []
        self.spent: list = []
        # The lane copy a chapter lane's thread is using. The usage logger, pricing and line
        # provenance hold the run's own wrapper and read it from the lane's thread (or a
        # request worker, which gets a copy of the lane's context), so they follow the lane.
        self.lane: contextvars.ContextVar[Any] = contextvars.ContextVar("slow_switch_lane", default=None)


class SlowSwitchEngine(FallbackEngine):
    """[primary, fallback]: the run moves to the fallback once the primary answers
    SLOW_REQUESTS_TO_SWITCH requests in a row slower than is_slow_request allows, and starts
    there while the primary was slow in the last SLOW_MEMORY_SECONDS. The move happens only
    in begin_chapter, which the pipeline calls when a chapter (or, with no chapters, a batch)
    starts, so a chapter is never translated by two engines. Errors are not a reason to
    switch (a fallback chain is for that), except that a fallback failing with one hands the
    run back to the primary."""

    def __init__(self, engines: list, choices: list, state: _SlowSwitchState | None = None):
        self._state = state or _SlowSwitchState(switched=recently_slow(engines[0]))
        super().__init__(engines, choices)
        # Not a chain's failed-usage hook: the pipeline then records a failed attempt itself.
        del self.failed_usage_cb
        self._active = 1 if self._state.switched else 0
        if state is None:
            self._state.spent = self.spent
            if self._state.switched:
                reason = _earlier_reason(engines[0])
                self._state.events.append({"from": choices[0], "to": choices[1], "reason": reason,
                                           "detail": OUT_OF_CREDIT_NOTICE if reason == OUT_OF_CREDIT
                                           else f"{choices[0]} was slow in an earlier run"})
        self.spent = self._state.spent
        self.events = self._state.events

    @property
    def active(self):
        lane = self._state.lane.get()
        if lane is not None and lane is not self:
            return lane._active
        return self._active

    @active.setter
    def active(self, index):
        self._active = index

    def fresh_run(self):
        """The same engines with a new run's state: whether it starts switched is decided
        now, from the switch memory, not when this object was built."""
        return SlowSwitchEngine(self.engines, self.choices)

    def lane_copy(self, clone):
        """A copy for one chapter lane: its own engine copies, the run's shared state."""
        return SlowSwitchEngine([clone(e) for e in self.engines], self.choices, self._state)

    def begin_chapter(self):
        s = self._state
        s.lane.set(self)
        if s.switched and not s.fallback_failed:
            self._active = max(self._active, 1)

    def translate_batch(self, zh_lines, context):
        # Each engine is called directly: its errors and retries stay what they are on a run
        # without the switch, and the pipeline records a failed attempt's usage as usual.
        on_primary = self.active == 0
        started = _clock()
        try:
            result = self.engines[self.active].translate_batch(zh_lines, context)
        except Exception as e:
            if on_primary and is_out_of_credit(e) and not self._state.fallback_failed:
                return self._leave_primary_out_of_credit(e, zh_lines, context)
            if on_primary or not _cannot_serve(e):
                raise
            # The fallback can't serve this PC (Ollama not running or not signed in): the
            # slow engine is still better than none.
            self._hand_back(e)
            return self.engines[0].translate_batch(zh_lines, context)
        if on_primary:
            self._note_primary_time(_clock() - started, len(zh_lines))
        return result

    def _leave_primary_out_of_credit(self, e, zh_lines, context):
        # Mid-chapter on purpose: the primary can't translate anything until it is topped up,
        # so staying would leave the rest of the chapter blank.
        s = self._state
        with s.lock:
            s.switched = True
            if not any(ev.get("reason") == OUT_OF_CREDIT for ev in s.events):
                s.events.append({"from": self.choices[0], "to": self.choices[1],
                                 "reason": OUT_OF_CREDIT, "detail": redact_secrets(str(e))[:200]})
        self.active = 1
        try:
            result = self.engines[1].translate_batch(zh_lines, context)
        except Exception as fallback_error:
            if not _cannot_serve(fallback_error):
                raise
            # With no fallback the user's problem is DeepSeek's credit, not a stopped Ollama.
            self._hand_back(fallback_error)
            raise e from None
        # Only once the fallback has served: otherwise the next run would skip DeepSeek and
        # waste a call on a fallback that isn't there before getting the same 402.
        _remember_slow(self.engines[0], OUT_OF_CREDIT)
        return result

    def _hand_back(self, fallback_error):
        with self._state.lock:
            self._state.fallback_failed = True
            self._state.events.append({"from": self.choices[1], "to": self.choices[0],
                                       "reason": type(fallback_error).__name__,
                                       "detail": redact_secrets(str(fallback_error))[:200]})
        self.active = 0

    def _note_primary_time(self, seconds, n_lines):
        s = self._state
        with s.lock:
            if not is_slow_request(seconds, n_lines):
                s.slow_in_a_row = 0
                return
            s.slow_in_a_row += 1
            if s.switched or s.fallback_failed or s.slow_in_a_row < SLOW_REQUESTS_TO_SWITCH:
                return
            s.switched = True
            s.events.append({"from": self.choices[0], "to": self.choices[1], "reason": "slow",
                             "detail": f"{s.slow_in_a_row} requests in a row were slow "
                                       f"(last {seconds:.0f} s for {n_lines} lines)"})
        _remember_slow(self.engines[0])
