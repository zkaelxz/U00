"""
sources/pacing.py -- per-source pace levels and the automatic slowdown.

Two separate things live here because both only ever make `PacingPolicy`
(sources/http.py) slower or faster for one source:

* A `PacingProfile` an adapter declares: what its careful / normal / fast
  levels mean, and the evidence that lets it offer `fast` at all. The person
  picks a level per source (`store.source_pace`).
* The session-long slowdown a source earns by pushing back (429, a challenge,
  a block, repeated timeouts), which is never lifted by the person's choice.

An adapter's `host_min_interval` is a floor that beats every level and every
relaxation; this module never lowers it.
"""

import threading
from dataclasses import dataclass, replace
from typing import Optional

LEVELS = ("careful", "normal", "fast")
DEFAULT_LEVEL = "normal"

# `careful` is a courtesy setting, not a vetted one, so it derives from
# `normal` instead of needing evidence of its own.
CAREFUL_FACTOR = 1.5
_CAREFUL_BREAKS = dict(session_break_min_requests=8, session_break_max_requests=20,
                       session_break_min_delay=30.0, session_break_max_delay=90.0)

MAX_SLOWDOWN = 8.0        # delays never grow past this multiple of the chosen level
QUIET_REQUESTS = 20       # clean requests that win back one step (halving)
TIMEOUT_STREAK = 3        # timeouts in a row that count as the site struggling
MAX_HOLD = 600.0          # longest Retry-After we honour for the next request


@dataclass(frozen=True)
class PaceLevel:
    """Overrides for one level. None keeps the global setting, so an adapter
    that declares nothing behaves exactly as it did before profiles existed."""
    min_delay: Optional[float] = None
    max_delay: Optional[float] = None
    max_concurrent: Optional[int] = None
    session_breaks: Optional[bool] = None


@dataclass(frozen=True)
class PacingProfile:
    careful: PaceLevel = PaceLevel()
    normal: PaceLevel = PaceLevel()
    fast: PaceLevel = PaceLevel()
    #: Where the numbers come from: robots.txt Crawl-delay / terms note and
    #: the date it was checked. Required for `fast_allowed`.
    evidence: str = ""
    fast_allowed: bool = False

    def __post_init__(self):
        if self.fast_allowed and not self.evidence.strip():
            raise ValueError("fast_allowed needs evidence: a vetted robots.txt or terms note")


DEFAULT_PROFILE = PacingProfile()


def effective_level(profile: Optional[PacingProfile], level: Optional[str]) -> str:
    """The level that actually applies: unknown names and an unvetted `fast`
    fall back to normal, so a stale saved value can never speed a source up."""
    if level not in LEVELS:
        return DEFAULT_LEVEL
    if level == "fast" and not (profile and profile.fast_allowed):
        return DEFAULT_LEVEL
    return level


def _override(policy, entry: PaceLevel):
    changes = {}
    if entry.min_delay is not None:
        changes["min_delay"] = max(0.0, float(entry.min_delay))
        changes["max_delay"] = max(changes["min_delay"], policy.max_delay)
    if entry.max_delay is not None:
        changes["max_delay"] = max(changes.get("min_delay", policy.min_delay), float(entry.max_delay))
    if entry.max_concurrent is not None:
        changes["max_concurrent"] = max(1, int(entry.max_concurrent))
    if entry.session_breaks is False:
        changes["session_break_min_requests"] = 0
    return replace(policy, **changes) if changes else policy


def apply_level(policy, profile: Optional[PacingProfile], level: Optional[str]):
    """`policy` (built from the global settings) adjusted to the chosen level.
    host_min_interval is left untouched, so the floor still wins in the client."""
    profile = profile or DEFAULT_PROFILE
    level = effective_level(profile, level)
    normal = _override(policy, profile.normal)
    if level == "normal":
        return normal
    if level == "fast":
        fast = _override(policy, profile.fast)
        # Fast is never slower than normal, whatever the adapter declares.
        return replace(fast, min_delay=min(fast.min_delay, normal.min_delay),
                       max_delay=min(fast.max_delay, normal.max_delay),
                       max_concurrent=max(fast.max_concurrent, normal.max_concurrent))
    careful = replace(
        normal, min_delay=normal.min_delay * CAREFUL_FACTOR,
        max_delay=normal.max_delay * CAREFUL_FACTOR, max_concurrent=1,
        session_break_min_delay=normal.session_break_min_delay * CAREFUL_FACTOR,
        session_break_max_delay=normal.session_break_max_delay * CAREFUL_FACTOR)
    careful = _override(careful, profile.careful)
    if careful.session_break_min_requests <= 0:   # careful always takes breaks
        careful = replace(careful, **_CAREFUL_BREAKS)
    # Careful is never faster than normal, even if the adapter declares so.
    return replace(careful, min_delay=max(careful.min_delay, normal.min_delay),
                   max_delay=max(careful.max_delay, normal.max_delay),
                   max_concurrent=min(careful.max_concurrent, normal.max_concurrent))


# -- automatic slowdown ---------------------------------------------------
# In memory only: "for the rest of the session" means a restart starts clean.
_lock = threading.Lock()
_slow = {}


def _entry(source: str) -> dict:
    return _slow.setdefault(source, {"mult": 1.0, "quiet": 0, "timeouts": 0, "hold_until": 0.0})


def reset_slowdown():
    with _lock:
        _slow.clear()


def multiplier(source: str) -> float:
    with _lock:
        return _slow.get(source, {}).get("mult", 1.0)


def is_slowed(source: str) -> bool:
    return multiplier(source) > 1.0


def hold_remaining(source: str, now: float) -> float:
    """Seconds left of a Retry-After the source sent; 0 when none."""
    with _lock:
        return max(0.0, _slow.get(source, {}).get("hold_until", 0.0) - now)


def note_trouble(source: str, kind: str, now: float, retry_after: Optional[float] = None,
                 escalate: bool = True) -> bool:
    """Records that the source pushed back. `kind` is "rate_limit", "challenge",
    "block" or "timeout". Returns True when the delays were doubled just now.
    `escalate=False` only records Retry-After (later retries of one request)."""
    with _lock:
        e = _entry(source)
        if retry_after:
            e["hold_until"] = max(e["hold_until"], now + min(float(retry_after), MAX_HOLD))
        e["quiet"] = 0
        if kind == "timeout":
            e["timeouts"] += 1
            if e["timeouts"] < TIMEOUT_STREAK:
                return False
            e["timeouts"] = 0
        if not escalate or e["mult"] >= MAX_SLOWDOWN:
            return False
        e["mult"] = min(MAX_SLOWDOWN, e["mult"] * 2)
        return True


def note_success(source: str) -> bool:
    """Counts one clean request; returns True when a step was just relaxed.
    Never goes below 1.0, so the chosen level (and the floor) stay in force."""
    with _lock:
        e = _slow.get(source)
        if e is None:
            return False
        e["timeouts"] = 0
        if e["mult"] <= 1.0:
            return False
        e["quiet"] += 1
        if e["quiet"] < QUIET_REQUESTS:
            return False
        e["quiet"] = 0
        e["mult"] = max(1.0, e["mult"] / 2)
        return True
