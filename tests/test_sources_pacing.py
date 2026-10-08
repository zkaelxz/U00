"""Per-source pace levels and the automatic slowdown (sources/pacing.py).
Offline: a scripted transport and a fake clock, no real sleeping."""

import pytest

from sources import pacing, store
from sources.base import SourceAdapter
from sources.http import PacingPolicy, SourceClient, reset_pacing_state
from sources.models import ChallengeDetected, FetchFailed
from sources.pacing import PaceLevel, PacingProfile

from .sources_helpers import FakeClock, FixedRng, ScriptedTransport, html

VETTED = PacingProfile(
    normal=PaceLevel(min_delay=4.0, max_delay=6.0),
    fast=PaceLevel(min_delay=1.0, max_delay=2.0, max_concurrent=2, session_breaks=False),
    evidence="robots.txt has no Crawl-delay; terms allow reading (checked 2026-10-08).",
    fast_allowed=True)


def _base(**kw):
    return PacingPolicy(**{"min_delay": 3.0, "max_delay": 8.0, "max_concurrent": 1,
                           **kw})


class TestLevels:
    def test_default_profile_changes_nothing_at_normal_and_fast_is_refused(self):
        p = _base()
        assert pacing.apply_level(p, pacing.DEFAULT_PROFILE, "normal") == p
        # An unvetted `fast` (even a stale saved value) is just normal.
        assert pacing.apply_level(p, pacing.DEFAULT_PROFILE, "fast") == p
        assert pacing.effective_level(pacing.DEFAULT_PROFILE, "fast") == "normal"
        assert pacing.effective_level(VETTED, "bogus") == "normal"

    def test_fast_needs_evidence(self):
        with pytest.raises(ValueError):
            PacingProfile(fast_allowed=True)

    def test_careful_is_slower_with_breaks_on(self):
        p = _base(session_break_min_requests=0)
        c = pacing.apply_level(p, pacing.DEFAULT_PROFILE, "careful")
        assert (c.min_delay, c.max_delay) == (4.5, 12.0)
        assert c.session_break_min_requests > 0 and c.max_concurrent == 1

    def test_careful_is_never_faster_than_normal(self):
        sneaky = PacingProfile(careful=PaceLevel(min_delay=0.1, max_delay=0.2, max_concurrent=4))
        n = pacing.apply_level(_base(max_concurrent=2), sneaky, "normal")
        c = pacing.apply_level(_base(max_concurrent=2), sneaky, "careful")
        assert c.min_delay >= n.min_delay and c.max_delay >= n.max_delay
        assert c.max_concurrent <= n.max_concurrent

    def test_vetted_levels(self):
        p = _base()
        n = pacing.apply_level(p, VETTED, "normal")
        f = pacing.apply_level(p, VETTED, "fast")
        assert (n.min_delay, n.max_delay) == (4.0, 6.0)
        assert (f.min_delay, f.max_delay, f.max_concurrent) == (1.0, 2.0, 2)
        assert f.session_break_min_requests == 0

    def test_fast_is_never_slower_than_normal(self):
        odd = PacingProfile(fast=PaceLevel(min_delay=30.0, max_delay=60.0), evidence="x",
                            fast_allowed=True)
        f = pacing.apply_level(_base(), odd, "fast")
        assert (f.min_delay, f.max_delay) == (3.0, 8.0)

    def test_host_floor_wins_over_every_level(self, isolated_db):
        reset_pacing_state()
        for level in pacing.LEVELS:
            clock = FakeClock()
            policy = pacing.apply_level(_base(min_delay=0.0, max_delay=0.0), VETTED, level)
            policy = PacingPolicy(**{**policy.__dict__, "min_delay": 0.0, "max_delay": 0.0,
                                     "session_break_min_requests": 0,
                                     "host_min_interval": {"floor.invalid": 10.0}})
            t = ScriptedTransport({"https://floor.invalid/1": html("a"),
                                   "https://floor.invalid/2": html("b")}, clock)
            c = SourceClient(f"floor-{level}", policy=policy, transport=t, sleep=clock.sleep,
                             clock=clock.clock, rng=FixedRng(0.0))
            c.get("https://floor.invalid/1")
            c.get("https://floor.invalid/2")
            assert t.calls[1]["t"] - t.calls[0]["t"] == pytest.approx(10.0)
            reset_pacing_state()

    def test_from_settings_uses_the_saved_level_only_with_a_profile(self, isolated_db):
        store.set_source_pace("vet", "fast")
        assert pacing.for_source(PacingPolicy.from_settings(), "vet", VETTED).min_delay == 1.0
        assert pacing.for_source(PacingPolicy.from_settings(), "vet", None).min_delay == 3.0   # no profile
        assert pacing.for_source(PacingPolicy.from_settings(), "vet", pacing.DEFAULT_PROFILE).min_delay == 3.0

    def test_adapter_default_is_the_neutral_profile(self):
        assert SourceAdapter.pacing_profile is pacing.DEFAULT_PROFILE
        assert not SourceAdapter.pacing_profile.fast_allowed


def _client(clock, transport, source="slow", **kw):
    reset_pacing_state()
    policy = PacingPolicy(**{"min_delay": 2.0, "max_delay": 2.0, "session_break_min_requests": 0,
                             "max_retries": 0, "host_min_interval": {}, **kw})
    return SourceClient(source, policy=policy, transport=transport, sleep=clock.sleep,
                        clock=clock.clock, rng=FixedRng(0.0))


def _gaps(t):
    return [round(b["t"] - a["t"], 3) for a, b in zip(t.calls, t.calls[1:])]


class TestSlowdown:
    def test_429_doubles_delays_and_notifies_once_per_request(self, isolated_db):
        clock = FakeClock()
        u = "https://s.invalid/"
        t = ScriptedTransport({u: [html("x", 429), html("ok")]}, clock)
        c = _client(clock, t, max_retries=3, backoff_base=0.0)
        c.get(u)    # 429 then ok: one retry, one doubling
        assert pacing.multiplier("slow") == 2.0
        assert c.stats["notice"] == "Slowed down: slow asked us to wait"
        c.get(u)
        assert _gaps(t)[-1] == pytest.approx(4.0)

    def test_retry_after_is_held_for_the_next_request(self, isolated_db):
        clock = FakeClock()
        u = "https://s.invalid/"
        t = ScriptedTransport({u: [html("x", 429, {"Retry-After": "30"}), html("ok")]}, clock)
        c = _client(clock, t, max_retries=0)
        with pytest.raises(FetchFailed):
            c.get(u)
        start = clock.clock()
        c.get(u)
        assert clock.clock() - start >= 30.0

    def test_challenge_is_not_retried_and_slows(self, isolated_db):
        clock = FakeClock()
        u = "https://cf.invalid/"
        t = ScriptedTransport({u: html("<title>Just a moment...</title>", 403,
                                       {"cf-mitigated": "challenge"})}, clock)
        c = _client(clock, t, source="cf", max_retries=3)
        with pytest.raises(ChallengeDetected):
            c.get(u)
        assert len(t.calls) == 1
        assert pacing.multiplier("cf") == 2.0

    def test_timeouts_slow_only_after_a_streak(self, isolated_db):
        clock = FakeClock()
        u = "https://s.invalid/"
        t = ScriptedTransport({u: [TimeoutError("t"), TimeoutError("t"), html("ok"),
                                   TimeoutError("t"), TimeoutError("t"), TimeoutError("t")]}, clock)
        c = _client(clock, t)
        for _ in range(2):
            with pytest.raises(FetchFailed):
                c.get(u)
        c.get(u)                       # a success resets the streak
        assert pacing.multiplier("slow") == 1.0
        for _ in range(3):
            with pytest.raises(FetchFailed):
                c.get(u)
        assert pacing.multiplier("slow") == 2.0

    def test_cap_and_relax_toward_the_chosen_level(self, isolated_db):
        clock = FakeClock()
        u = "https://s.invalid/"
        t = ScriptedTransport({u: html("ok")}, clock)
        c = _client(clock, t)
        for _ in range(6):
            pacing.note_trouble("slow", "rate_limit", clock.clock())
        assert pacing.multiplier("slow") == pacing.MAX_SLOWDOWN
        for _ in range(pacing.QUIET_REQUESTS):
            c.get(u)
        assert pacing.multiplier("slow") == pacing.MAX_SLOWDOWN / 2
        for _ in range(pacing.QUIET_REQUESTS * 5):
            c.get(u)
        assert pacing.multiplier("slow") == 1.0     # never below the chosen level

    def test_slowdown_scales_the_host_floor_not_below_it(self, isolated_db):
        clock = FakeClock()
        t = ScriptedTransport({"https://f.invalid/": html("ok")}, clock)
        c = _client(clock, t, source="f", min_delay=0.0, max_delay=0.0,
                    host_min_interval={"f.invalid": 10.0})
        c.get("https://f.invalid/")
        c.get("https://f.invalid/")
        assert _gaps(t) == [10.0]
        pacing.note_trouble("f", "block", clock.clock())
        c.get("https://f.invalid/")
        assert _gaps(t)[-1] == pytest.approx(20.0)

    def test_notice_carries_no_url(self, isolated_db):
        clock = FakeClock()
        u = "https://s.invalid/book?token=SECRET"
        t = ScriptedTransport({u: html("x", 429)}, clock)
        c = _client(clock, t)
        with pytest.raises(FetchFailed):
            c.get(u)
        assert "SECRET" not in c.stats["notice"] and "http" not in c.stats["notice"]
