"""
tests/test_sources_core.py -- Step 23: the adapter interface, the paced
client, health/backoff, the cache, the access ladder and diagnostics.

All offline: a scripted transport stands in for the network, and a fake
clock stands in for real waiting.
"""

import pytest

from sources import cache as cache_mod
from sources import health, ladder, store
from sources.base import SourceAdapter
from sources.http import PacingPolicy, SourceClient, _requests_transport, reset_pacing_state
from sources.models import (AccessTier, CapabilityStatus, ChallengeDetected, ChapterInfo,
                            ContentAccess, FailureReason, FetchFailed, NotSupportedError,
                            PageRef, SearchResult, SeriesInfo, SourceCapabilities,
                            SourceUnavailable, TechnicalStatus, TermsProhibited)

from .sources_helpers import FakeClock, FixedRng, ScriptedTransport, html, make_client


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------

class PartialAdapter(SourceAdapter):
    """Implements only search/get_series/get_chapters/get_pages -- no
    login, no download_page, no chapter text."""
    name = "partial"
    display_name = "Partial"
    content_types = ["manhua"]

    def search(self, query, page=1):
        return [SearchResult(self.name, "s1", f"Result for {query}")]

    def get_series(self, series_id):
        return SeriesInfo(self.name, series_id, "Series One")

    def get_chapters(self, series_id):
        return [ChapterInfo(self.name, series_id, "c1", "第1话")]

    def get_pages(self, chapter):
        return [PageRef(self.name, chapter.chapter_id, 0, "https://x.invalid/1.png")]


class TestAdapterInterface:
    def test_partial_adapter_works_through_the_interface(self, isolated_db):
        a = PartialAdapter(client=make_client("partial", ScriptedTransport()))
        assert a.search("abc")[0].title == "Result for abc"
        assert a.get_series("s1").title == "Series One"
        chapters = a.get_chapters("s1")
        assert [c.chapter_id for c in chapters] == ["c1"]
        assert a.get_pages(chapters[0])[0].url.endswith("1.png")

    def test_unimplemented_optional_methods_fail_cleanly(self, isolated_db):
        a = PartialAdapter(client=make_client("partial", ScriptedTransport()))
        for call in (lambda: a.login(), lambda: a.refresh_session(),
                     lambda: a.download_page(None), lambda: a.get_chapter_text(None)):
            with pytest.raises(NotSupportedError) as e:
                call()
            assert "Partial" in str(e.value)

    def test_supports_reports_what_is_overridden(self, isolated_db):
        a = PartialAdapter(client=make_client("partial", ScriptedTransport()))
        assert a.supports("search") and a.supports("get_pages")
        assert not a.supports("login") and not a.supports("download_page")

    def test_capabilities_start_untested(self, isolated_db):
        caps = PartialAdapter(client=make_client("partial", ScriptedTransport())).capabilities()
        assert caps.status == CapabilityStatus.UNTESTED.value
        assert all(not t.tested for t in caps.tiers.values())
        assert caps.technical == {} and caps.terms == {}


# ---------------------------------------------------------------------------
# Pacing, retries, challenges
# ---------------------------------------------------------------------------

class TestPacing:
    def test_default_pacing_is_applied_to_a_multi_page_fetch(self, isolated_db):
        reset_pacing_state()
        clock = FakeClock()
        urls = [f"https://site.invalid/p{i}.png" for i in range(5)]
        t = ScriptedTransport({u: html("x") for u in urls}, clock)
        policy = PacingPolicy.from_settings()
        assert (policy.min_delay, policy.max_delay, policy.max_concurrent, policy.max_retries) == \
            (3.0, 8.0, 1, 3)
        c = SourceClient("paced", policy=policy, transport=t, sleep=clock.sleep,
                         clock=clock.clock, rng=FixedRng(0.5))
        for u in urls:
            c.get(u)
        times = [call["t"] for call in t.calls]
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert gaps == pytest.approx([5.5] * 4)      # midpoint of the 3-8s default
        assert c.snapshot()["requests"] == 5

    def test_changing_settings_changes_the_pacing(self, isolated_db):
        store.set_setting("pace_min_delay", 5.0)
        store.set_setting("pace_max_delay", 5.0)
        store.set_setting("max_retries", 1)
        reset_pacing_state()
        clock = FakeClock()
        t = ScriptedTransport({"https://a.invalid/1": html("x"), "https://a.invalid/2": html("y")},
                              clock)
        c = SourceClient("paced2", policy=PacingPolicy.from_settings(), transport=t,
                         sleep=clock.sleep, clock=clock.clock, rng=FixedRng(0.0))
        c.get("https://a.invalid/1")
        c.get("https://a.invalid/2")
        assert t.calls[1]["t"] - t.calls[0]["t"] == pytest.approx(5.0)
        assert c.policy.max_retries == 1

    def test_session_break_is_on_by_default(self):
        """Direct construction (bypassing make_client's test-only override
        below) gets the real, human-like default: a longer break every
        8-20 requests, not a constant per-request rate for a whole run."""
        policy = PacingPolicy()
        assert policy.session_break_min_requests == 8
        assert policy.session_break_max_requests == 20
        assert (policy.session_break_min_delay, policy.session_break_max_delay) == (30.0, 90.0)
        assert PacingPolicy.from_settings().session_break_min_requests == 8

    def test_a_long_running_session_takes_a_human_like_break(self, isolated_db):
        """Step 90 follow-up: a session shouldn't be an evenly spaced
        request rate for its whole length -- every so often it pauses
        longer, like a person setting the app down and coming back."""
        clock = FakeClock()
        urls = [f"https://session.invalid/p{i}" for i in range(7)]
        t = ScriptedTransport({u: html("x") for u in urls}, clock)
        c = make_client("session_break", t, clock, rng=FixedRng(0.0),
                        session_break_min_requests=3, session_break_max_requests=3,
                        session_break_min_delay=45.0, session_break_max_delay=45.0)
        for u in urls:
            c.get(u)
        gaps = [b["t"] - a["t"] for a, b in zip(t.calls, t.calls[1:])]
        # Ordinary gap is 0 (min_delay=max_delay=0 from make_client); the
        # break lands before the 4th and 7th requests (every 3 requests).
        assert gaps == pytest.approx([0.0, 0.0, 45.0, 0.0, 0.0, 45.0])

    def test_zero_disables_the_session_break(self, isolated_db):
        clock = FakeClock()
        urls = [f"https://no_break.invalid/p{i}" for i in range(25)]
        t = ScriptedTransport({u: html("x") for u in urls}, clock)
        c = make_client("no_break", t, clock, rng=FixedRng(0.0))   # make_client's own default
        for u in urls:
            c.get(u)
        assert all(b["t"] == a["t"] for a, b in zip(t.calls, t.calls[1:]))

    def test_adapter_host_minimum_overrides_a_shorter_default(self, isolated_db):
        clock = FakeClock()
        t = ScriptedTransport({"https://slow.invalid/1": html("x"),
                               "https://slow.invalid/2": html("y")}, clock)
        c = make_client("slow", t, clock, min_delay=1.0, max_delay=1.0,
                        host_min_interval={"slow.invalid": 10.0})
        c.get("https://slow.invalid/1")
        c.get("https://slow.invalid/2")
        assert t.calls[1]["t"] - t.calls[0]["t"] == pytest.approx(10.0)

    def test_429_and_5xx_retry_with_exponential_backoff_up_to_the_cap(self, isolated_db):
        clock = FakeClock()
        u = "https://busy.invalid/x"
        t = ScriptedTransport({u: [html("slow down", 429), html("oops", 503), html("oops", 502),
                                   html("oops", 500)]}, clock)
        c = make_client("busy", t, clock, max_retries=3, backoff_base=2.0)
        with pytest.raises(FetchFailed):
            c.get(u)
        assert len(t.calls) == 4                     # 1 try + 3 retries, then stop
        gaps = [b["t"] - a["t"] for a, b in zip(t.calls, t.calls[1:])]
        assert gaps == pytest.approx([2.0, 4.0, 8.0])

    def test_retry_then_success(self, isolated_db):
        clock = FakeClock()
        u = "https://busy.invalid/y"
        t = ScriptedTransport({u: [html("slow down", 429), html("<p>ok</p>" * 100)]}, clock)
        c = make_client("busy2", t, clock)
        assert c.get(u).status_code == 200
        assert len(t.calls) == 2

    def test_challenge_stops_immediately_with_no_second_request(self, isolated_db):
        clock = FakeClock()
        u = "https://cf.invalid/chapter/1"
        t = ScriptedTransport({u: html("<title>Just a moment...</title>", 403,
                                       {"cf-mitigated": "challenge"})}, clock)
        c = make_client("cf", t, clock, max_retries=3)
        with pytest.raises(ChallengeDetected) as e:
            c.get(u)
        assert len(t.calls) == 1
        assert e.value.reason == FailureReason.CLOUDFLARE_CHALLENGE
        assert e.value.url == u

    def test_every_request_has_a_timeout(self, isolated_db):
        seen = []

        def transport(method, url, headers, data, timeout):
            seen.append(timeout)
            return html("<p>ok</p>")
        make_client("to", transport).get("https://t.invalid/")
        assert seen and seen[0] > 0


# ---------------------------------------------------------------------------
# get_with_mirrors: the mirror that actually worked is preferred next time
# ---------------------------------------------------------------------------

class TestMirrors:
    def test_a_later_call_tries_the_mirror_that_worked_last_time_first(self, isolated_db):
        import requests
        mirrors = ["https://a.invalid", "https://b.invalid", "https://c.invalid"]
        clock = FakeClock()
        t = ScriptedTransport({
            "https://a.invalid/x": requests.ConnectionError("down"),
            "https://b.invalid/x": html("<p>ok</p>"),
        }, clock)
        c = make_client("mir", t, clock, max_retries=0)
        c.get_with_mirrors("/x", mirrors)
        assert t.urls() == ["https://a.invalid/x", "https://b.invalid/x"]
        # A second call, even against a fresh route set, should try the
        # mirror that won last time (b) before falling back to a or c.
        t.routes["https://b.invalid/x"] = html("<p>ok again</p>")
        c.get_with_mirrors("/x", mirrors)
        assert t.urls()[-1] == "https://b.invalid/x"

    def test_a_preferred_mirror_no_longer_in_the_list_is_ignored(self, isolated_db):
        import requests
        clock = FakeClock()
        t = ScriptedTransport({
            "https://a.invalid/x": requests.ConnectionError("down"),
            "https://b.invalid/x": html("<p>ok</p>"),
        }, clock)
        c = make_client("mir2", t, clock, max_retries=0)
        c.get_with_mirrors("/x", ["https://a.invalid", "https://b.invalid"])
        # b won and is now preferred; call again with a mirror list that no
        # longer contains b at all -- the preference should be ignored
        # rather than crash or insert a mirror never configured for this call.
        t.routes["https://c.invalid/x"] = html("<p>ok</p>")
        c.get_with_mirrors("/x", ["https://c.invalid"])
        assert t.urls()[-1] == "https://c.invalid/x"

    def test_reset_pacing_state_forgets_the_preferred_mirror(self, isolated_db):
        import requests
        mirrors = ["https://a.invalid", "https://b.invalid"]
        clock = FakeClock()
        t = ScriptedTransport({
            "https://a.invalid/x": requests.ConnectionError("down"),
            "https://b.invalid/x": html("<p>ok</p>"),
        }, clock)
        c = make_client("mir3", t, clock, max_retries=0)
        c.get_with_mirrors("/x", mirrors)
        reset_pacing_state()
        t.routes["https://a.invalid/x"] = html("<p>ok</p>")
        c.get_with_mirrors("/x", mirrors)
        # Back to trying a first, since the preference was forgotten.
        assert t.urls()[-1] == "https://a.invalid/x"


# ---------------------------------------------------------------------------
# Health: 🔴 sources wait out their backoff
# ---------------------------------------------------------------------------

class TestHealth:
    def test_red_source_is_not_retried_faster_than_its_backoff(self, isolated_db, monkeypatch):
        now = {"t": 50_000.0}
        monkeypatch.setattr("sources.health.time.time", lambda: now["t"])
        store.set_setting("unavailable_backoff", 600.0)
        u = "https://down.invalid/"
        t = ScriptedTransport({u: html("down", 500)})
        c = make_client("down", t, max_retries=0)
        for _ in range(health.RED_AFTER):
            with pytest.raises(FetchFailed):
                c.get(u)
        assert health.light("down") == health.RED
        calls_before = len(t.calls)

        now["t"] += 599
        with pytest.raises(SourceUnavailable) as e:
            c.get(u)
        assert len(t.calls) == calls_before           # not contacted at all
        assert e.value.retry_after == pytest.approx(1.0)

        now["t"] += 2
        with pytest.raises(FetchFailed):
            c.get(u)                                   # allowed again, and still failing
        assert len(t.calls) == calls_before + 1
        # the next window is longer (doubling), not the same
        assert health.retry_after("down") == pytest.approx(1200.0)

    def test_success_turns_the_light_green(self, isolated_db):
        health.record_failure("s", "HTTP_ERROR", "x", base_backoff=10)
        assert health.light("s") == health.YELLOW
        health.record_success("s", 0.2)
        assert health.light("s") == health.GREEN


# ---------------------------------------------------------------------------
# Cache modes
# ---------------------------------------------------------------------------

class TestCache:
    URL = "https://c.invalid/page1.png"

    def _cycle(self, mode):
        c = cache_mod.RawCache(mode)
        c.put(self.URL, b"page-bytes")
        during = c.get(self.URL)
        c.release()
        return during, cache_mod.RawCache(mode).get(self.URL)

    def test_none_retains_nothing(self, isolated_db):
        assert self._cycle("none") == (None, None)

    def test_temporary_is_cleared_after_use(self, isolated_db):
        assert self._cycle("temporary") == (b"page-bytes", None)

    def test_keep_translated_clears_originals_after_use(self, isolated_db):
        assert self._cycle("keep_translated") == (b"page-bytes", None)

    @pytest.mark.parametrize("mode", ["keep_originals", "keep_both"])
    def test_keep_modes_keep(self, isolated_db, mode):
        assert self._cycle(mode) == (b"page-bytes", b"page-bytes")

    def test_release_drops_only_the_callers_own_downloads(self, isolated_db):
        a, b = cache_mod.RawCache("temporary"), cache_mod.RawCache("temporary")
        a.put("https://a.invalid/1", b"a-bytes")
        b.put("https://b.invalid/1", b"b-bytes")
        a.release()
        assert cache_mod.RawCache("temporary").get("https://a.invalid/1") is None
        assert b.get("https://b.invalid/1") == b"b-bytes"
        b.release()
        assert cache_mod.RawCache("temporary").get("https://b.invalid/1") is None

    def test_a_url_both_imports_hold_survives_until_the_last_release(self, isolated_db):
        a, b = cache_mod.RawCache("temporary"), cache_mod.RawCache("temporary")
        a.put(self.URL, b"page-bytes")
        b.put(self.URL, b"page-bytes")
        a.release()
        assert b.get(self.URL) == b"page-bytes"
        b.release()
        assert cache_mod.RawCache("temporary").get(self.URL) is None

    def test_release_sweeps_day_old_leftovers_of_a_crashed_import(self, isolated_db, monkeypatch):
        crashed = cache_mod.RawCache("temporary")
        crashed.put("https://old.invalid/1", b"old")
        del crashed   # its import died without releasing: nothing holds the row any more
        mine = cache_mod.RawCache("temporary")
        mine.put("https://new.invalid/1", b"new")
        other = cache_mod.RawCache("temporary")   # a long-running live import
        other.put("https://other.invalid/1", b"other")
        now = cache_mod.time.time()
        monkeypatch.setattr(cache_mod.time, "time", lambda: now + 2 * 86400)
        mine.release()
        with store.connect() as conn:
            urls = [r["url"] for r in conn.execute("SELECT url FROM cache_index")]
        assert urls == ["https://other.invalid/1"]
        assert other.get("https://other.invalid/1") == b"other"
        other.release()

    def test_same_content_stored_once(self, isolated_db):
        import os
        c = cache_mod.RawCache("keep_originals")
        c.put("https://a.invalid/1", b"same")
        c.put("https://b.invalid/1", b"same")
        files = [f for _, _, fs in os.walk(store.cache_dir()) for f in fs]
        assert len(files) == 1
        assert c.stats()["entries"] == 2

    def test_client_serves_cache_hits_without_a_request(self, isolated_db):
        u = "https://c.invalid/p.png"
        t = ScriptedTransport({u: html("<p>x</p>")})
        c = make_client("cached", t)
        c.cache = cache_mod.RawCache("keep_originals")
        c.get(u)
        r = c.get(u)
        assert r.from_cache and len(t.calls) == 1
        assert c.snapshot()["cache_hits"] == 1

    def test_unknown_mode_is_rejected(self, isolated_db):
        with pytest.raises(ValueError):
            cache_mod.RawCache("forever")


class TestCacheCeiling:
    """Roadmap 111: the keep modes' raw cache is trimmed to cache_max_mb,
    least recently used content first."""
    MB = 1024 * 1024

    @pytest.fixture(autouse=True)
    def ticking_clock(self, monkeypatch):
        now = [1000.0]

        def tick():
            now[0] += 1
            return now[0]
        monkeypatch.setattr(cache_mod.time, "time", tick)

    def _mb(self, n_bytes):
        return n_bytes / self.MB

    def _urls(self):
        with store.connect() as conn:
            return sorted(r["url"] for r in conn.execute("SELECT url FROM cache_index"))

    def _files(self):
        import os
        return sorted(f for _, _, fs in os.walk(store.cache_dir()) for f in fs)

    def test_oldest_used_goes_first(self, isolated_db):
        c = cache_mod.RawCache("keep_originals")
        for name in "abc":
            c.put(f"https://c.invalid/{name}", name.encode() * 100)
        assert c.enforce_ceiling(max_mb=self._mb(200)) == 1
        assert self._urls() == ["https://c.invalid/b", "https://c.invalid/c"]
        assert len(self._files()) == 2

    def test_a_hit_refreshes_recency(self, isolated_db):
        c = cache_mod.RawCache("keep_both")
        for name in "abc":
            c.put(f"https://c.invalid/{name}", name.encode() * 100)
        assert c.get("https://c.invalid/a") == b"a" * 100
        c.enforce_ceiling(max_mb=self._mb(200))
        assert self._urls() == ["https://c.invalid/a", "https://c.invalid/c"]

    def test_shared_content_counted_once_and_removed_with_all_its_urls(self, isolated_db):
        c = cache_mod.RawCache("keep_originals")
        c.put("https://a.invalid/1", b"x" * 100)
        c.put("https://b.invalid/1", b"x" * 100)
        c.put("https://c.invalid/1", b"y" * 100)
        assert c.stats() == {"entries": 3, "bytes": 200, "mode": "keep_originals"}
        assert c.enforce_ceiling(max_mb=self._mb(200)) == 0
        assert len(self._urls()) == 3
        assert c.enforce_ceiling(max_mb=self._mb(100)) == 1
        assert self._urls() == ["https://c.invalid/1"]
        assert c.get("https://a.invalid/1") is None

    def test_content_an_import_still_holds_is_kept(self, isolated_db):
        keep = cache_mod.RawCache("keep_originals")
        keep.put("https://a.invalid/1", b"x" * 100)
        cache_mod.RawCache("temporary").put("https://b.invalid/1", b"x" * 100)
        keep.put("https://c.invalid/1", b"y" * 100)
        assert keep.enforce_ceiling(max_mb=self._mb(1)) == 1
        assert self._urls() == ["https://a.invalid/1", "https://b.invalid/1"]
        assert keep.get("https://a.invalid/1") == b"x" * 100

    def test_zero_means_no_limit(self, isolated_db):
        c = cache_mod.RawCache("keep_originals")
        for name in "abc":
            c.put(f"https://c.invalid/{name}", name.encode() * 100)
        assert store.get_setting("cache_max_mb") == 0
        assert c.enforce_ceiling() == 0
        assert c.enforce_ceiling(max_mb=0) == 0
        assert len(self._urls()) == 3

    def test_setting_is_read_in_megabytes(self, isolated_db):
        c = cache_mod.RawCache("keep_originals")
        c.put("https://c.invalid/a", b"a" * (600 * 1024))
        c.put("https://c.invalid/b", b"b" * (600 * 1024))
        store.set_setting("cache_max_mb", 1)
        assert c.enforce_ceiling() == 1
        assert self._urls() == ["https://c.invalid/b"]

    def test_temporary_mode_is_untouched(self, isolated_db):
        c = cache_mod.RawCache("temporary")
        for name in "abc":
            c.put(f"https://c.invalid/{name}", name.encode() * 100)
        assert c.enforce_ceiling(max_mb=self._mb(1)) == 0
        assert len(self._urls()) == 3
        c.release()
        assert self._urls() == []

    def test_drama_pages_survive(self, isolated_db):
        import os

        import db
        page = os.path.join(db.DRAMAS_DIR, "1", "pages", "0001.png")
        os.makedirs(os.path.dirname(page), exist_ok=True)
        with open(page, "wb") as f:
            f.write(b"p" * 500)
        c = cache_mod.RawCache("keep_originals")
        c.put("https://c.invalid/a", b"a" * 100)
        c.enforce_ceiling(max_mb=self._mb(1))
        assert self._urls() == [] and os.path.exists(page)

    def test_stale_unindexed_files_are_dropped(self, isolated_db, monkeypatch):
        """Crashed put() leftovers (.part) and files an earlier trim couldn't
        remove, once a day old; indexed and fresh files stay."""
        import os
        now = 10 * 86400.0
        monkeypatch.setattr(cache_mod.time, "time", lambda: now)
        c = cache_mod.RawCache("keep_originals")
        c.put("https://c.invalid/a", b"a" * 100)
        root = store.cache_dir()
        os.makedirs(os.path.join(root, "ab"), exist_ok=True)
        old_part = os.path.join(root, "ab", "old.part")
        fresh_part = os.path.join(root, "ab", "fresh.part")
        old_orphan = os.path.join(root, "ab", "ab" + "0" * 62)
        fresh_orphan = os.path.join(root, "ab", "ab" + "1" * 62)
        for p, mtime in ((old_part, now - 86400 - 60), (fresh_part, now - 60),
                         (old_orphan, now - 86400 - 60), (fresh_orphan, now - 60)):
            with open(p, "wb") as f:
                f.write(b"half")
            os.utime(p, (mtime, mtime))
        indexed = c._path(__import__("hashlib").sha256(b"a" * 100).hexdigest())
        os.utime(indexed, (now - 5 * 86400, now - 5 * 86400))
        c.enforce_ceiling()
        assert not os.path.exists(old_part) and not os.path.exists(old_orphan)
        assert os.path.exists(fresh_part) and os.path.exists(fresh_orphan)
        assert os.path.exists(indexed)

    def test_a_file_that_cannot_be_removed_does_not_stop_the_trim(self, isolated_db,
                                                                   monkeypatch):
        c = cache_mod.RawCache("keep_originals")
        for name in "abc":
            c.put(f"https://c.invalid/{name}", name.encode() * 100)
        real_remove = cache_mod.os.remove
        a_path = c._path(__import__("hashlib").sha256(b"a" * 100).hexdigest())

        def remove(path):
            if path == a_path:
                raise PermissionError("in use")
            real_remove(path)
        monkeypatch.setattr(cache_mod.os, "remove", remove)
        assert c.enforce_ceiling(max_mb=self._mb(100)) == 1
        assert self._urls() == ["https://c.invalid/c"]

    def test_temporary_bytes_do_not_count_against_the_ceiling(self, isolated_db):
        keep = cache_mod.RawCache("keep_originals")
        keep.put("https://a.invalid/1", b"a" * 100)
        cache_mod.RawCache("temporary").put("https://b.invalid/1", b"t" * 1000)
        assert keep.enforce_ceiling(max_mb=self._mb(100)) == 0
        assert len(self._urls()) == 2

    def test_a_file_removed_under_a_hit_is_a_miss(self, isolated_db):
        import os
        c = cache_mod.RawCache("keep_originals")
        c.put("https://c.invalid/a", b"a" * 100)
        with store.connect() as conn:
            sha = conn.execute("SELECT sha256 FROM cache_index").fetchone()["sha256"]
        os.remove(c._path(sha))
        assert c.get("https://c.invalid/a") is None and self._urls() == []

    def test_lowering_the_setting_trims_now(self, isolated_db, monkeypatch):
        from services import sources_registry_service as svc
        monkeypatch.setattr(svc.src_http, "reset_pacing_state", lambda: None)
        c = cache_mod.RawCache("keep_originals")
        c.put("https://c.invalid/a", b"a" * (600 * 1024))
        c.put("https://c.invalid/b", b"b" * (600 * 1024))
        out = svc.update_settings({"cache_max_mb": 1})
        assert out["cache_max_mb"] == 1
        assert self._urls() == ["https://c.invalid/b"]


# ---------------------------------------------------------------------------
# The access ladder and diagnostics
# ---------------------------------------------------------------------------

def _tier(ok, reasons=(), html_text="", log=None, name=None, **kw):
    def run(url):
        if log is not None:
            log.append(name)
        return ladder.TierOutcome(ok, html=html_text, reasons=list(reasons), **kw)
    return run


class TestLadder:

    @pytest.fixture(autouse=True)
    def _public_dns(self, monkeypatch):
        # B-28: an unresolvable start URL drops the browser tiers; these
        # fake `.invalid` hosts stand for public sites, so resolve them.
        monkeypatch.setattr("services.url_guard.resolve_public", lambda url: "93.184.216.34")

    def test_tries_each_level_in_order_and_stops_at_first_success(self, isolated_db):
        log = []
        tiers = {
            AccessTier.OFFICIAL_API: _tier(True, log=log, name="api"),
            AccessTier.AUTHENTICATED_BROWSER: _tier(True, log=log, name="auth"),
            AccessTier.RENDERED_BROWSER: _tier(True, html_text="<p>ok</p>", log=log, name="render"),
            AccessTier.STATIC_HTTP: _tier(False, [FailureReason.ACCESS_DENIED], log=log,
                                          name="static"),
        }
        r = ladder.run_ladder("https://x.invalid/", tiers)
        assert log == ["static", "render"]
        assert r.tier == AccessTier.RENDERED_BROWSER.value
        assert r.technical_status == TechnicalStatus.BROWSER_ACCESSIBLE.value

    def test_static_failure_automatically_tries_the_browser(self, isolated_db):
        u = "https://blocked.invalid/ch1"
        t = ScriptedTransport({u: html("forbidden", 403)})
        client = make_client("blocked", t, max_retries=0)
        rendered = []

        def fake_render(url):
            rendered.append(url)
            return "<html><body>" + "<p>real text</p>" * 80 + "</body></html>", "real text"
        r = ladder.run_ladder(u, {
            AccessTier.STATIC_HTTP: ladder.static_tier(client),
            AccessTier.RENDERED_BROWSER: ladder.rendered_tier(client, fake_render)})
        assert rendered == [u]
        lines = r.summary_lines()
        assert lines[0].startswith("Static HTTP: FAILED -- ACCESS_DENIED -- HTTP 403")
        assert lines[1].startswith("Browser: SUCCESS")

    def test_challenge_hands_off_and_never_runs_an_automated_browser(self, isolated_db):
        u = "https://cf.invalid/x"
        t = ScriptedTransport({u: html("<title>Just a moment...</title>", 403,
                                       {"cf-mitigated": "challenge"})})
        client = make_client("cf2", t)
        rendered = []
        r = ladder.run_ladder(u, {
            AccessTier.STATIC_HTTP: ladder.static_tier(client),
            AccessTier.RENDERED_BROWSER: ladder.rendered_tier(
                client, lambda url: rendered.append(url) or ("", ""))})
        assert r.handoff == {"tier": "STATIC_HTTP", "reason": "CLOUDFLARE_CHALLENGE", "url": u}
        assert rendered == [] and len(t.calls) == 1
        assert r.capability_status == CapabilityStatus.MANUAL_VERIFICATION_REQUIRED.value

    def test_user_assisted_page_resumes_after_a_handoff(self, isolated_db):
        page = "<html><body>" + "<p>chapter text</p>" * 60 + "</body></html>"
        r = ladder.run_ladder("https://cf.invalid/x",
                              {AccessTier.USER_ASSISTED_BROWSER: ladder.user_assisted_tier(page)})
        assert r.ok and r.tier == AccessTier.USER_ASSISTED_BROWSER.value
        still_challenge = ladder.run_ladder(
            "https://cf.invalid/x", {AccessTier.USER_ASSISTED_BROWSER: ladder.user_assisted_tier(
                '<html><div class="cf-turnstile"></div></html>')})
        assert not still_challenge.ok

    def test_metadata_only_official_api_is_a_partial_result(self, isolated_db):
        r = ladder.run_ladder("https://ncode.invalid/n1", {
            AccessTier.STATIC_HTTP: _tier(False, [FailureReason.JAVASCRIPT_REQUIRED,
                                                  FailureReason.EMPTY_SPA_SHELL]),
            AccessTier.RENDERED_BROWSER: _tier(False, [FailureReason.NOT_INSTALLED]),
            AccessTier.OFFICIAL_API: _tier(True, partial=True,
                                           content_access=ContentAccess.METADATA.value,
                                           detail="metadata only; no chapter text"),
        })
        assert r.ok and r.partial
        assert r.tier == AccessTier.OFFICIAL_API.value
        assert r.technical_status == TechnicalStatus.PARTIALLY_SUPPORTED.value
        assert r.capability_status == CapabilityStatus.PARTIALLY_SUPPORTED.value
        assert r.content_access == ContentAccess.METADATA.value

    def test_diagnostics_name_the_tier_and_reason(self, isolated_db):
        u = "https://two.invalid/c"
        t = ScriptedTransport({u: html("nope", 403)})
        client = make_client("two", t, max_retries=0)
        challenge_page = ('<html><head><title>Just a moment...</title></head>'
                          '<body><div class="cf-turnstile"></div></body></html>')
        r = ladder.run_ladder(u, {
            AccessTier.STATIC_HTTP: ladder.static_tier(client),
            AccessTier.RENDERED_BROWSER: ladder.rendered_tier(
                client, lambda url: (challenge_page, ""))}, source="two")
        assert r.handoff["tier"] == "RENDERED_BROWSER"
        assert r.summary_lines()[-1] == \
            "Stopped: BOT_CHALLENGE detected at Browser -- handed to you."
        logged = store.recent_attempts("two")[0]
        assert "BOT_CHALLENGE" in logged["reasons"] and "blocked" not in " ".join(logged["lines"])

    def test_authenticated_tier_names_a_still_signed_out_session(self, isolated_db):
        """Step 23k's tier: a saved profile that still sees a login form
        fails with that exact reason -- nothing is extracted from it."""
        login = ('<html><body><form><p>Please log in</p><input type="password"></form>'
                 '</body></html>')
        seen = []
        r = ladder.run_ladder("https://x.invalid/", {
            AccessTier.AUTHENTICATED_BROWSER: ladder.authenticated_tier(
                "/profiles/x", fetch_with_profile=lambda u, d: (seen.append(d), (login, ""))[1])})
        assert seen == ["/profiles/x"]
        assert r.attempts[-1].reason == "AUTHENTICATION_REQUIRED" and not r.ok


class TestRealCaseMatrix:

    @pytest.fixture(autouse=True)
    def _public_dns(self, monkeypatch):
        # B-28: an unresolvable start URL drops the browser tiers; these
        # fake `.invalid` hosts stand for public sites, so resolve them.
        monkeypatch.setattr("services.url_guard.resolve_public", lambda url: "93.184.216.34")

    """Shapes of the real cases the roadmap's vetting actually hit."""

    def _static_only(self, source, routes, url, max_retries=0):
        client = make_client(source, ScriptedTransport(routes), max_retries=max_retries)
        return ladder.run_ladder(url, {AccessTier.STATIC_HTTP: ladder.static_tier(client)})

    def test_baozimh_shaped_gatekeeper_is_blocked_here_not_disqualified(self, isolated_db):
        urls = ["https://www.baozimh.invalid/", "https://www.baozimh.invalid/comic/x"]
        results = [self._static_only("baozimh", {u: html("403 Forbidden", 403)}, u) for u in urls]
        for r in results:
            assert r.technical_status == TechnicalStatus.BLOCKED_IN_CURRENT_ENVIRONMENT.value
            assert r.technical_status != TechnicalStatus.DISQUALIFIED.value
            assert r.reasons == [FailureReason.ACCESS_DENIED]

    def test_manhuaku_shaped_page_routes_to_browser_and_is_never_decoded(self, isolated_db):
        u = "https://manhuaku.invalid/chapter/1"
        page = ('<html><head><script src="/js/crypto-js.min.js"></script>'
                '<script src="/js/vue.min.js"></script></head><body><div id="app">'
                '<img v-for="p in pages" :src="p"></div>'
                '<script>var d=CryptoJS.AES.decrypt(enc,key);</script></body></html>')
        client = make_client("manhuaku", ScriptedTransport({u: html(page)}))
        rendered = []

        def fake_render(url):
            rendered.append(url)
            return "<html><body>" + '<img src="/p1.jpg">' * 3 + "<p>x</p>" * 200 + "</body></html>", ""
        r = ladder.run_ladder(u, {AccessTier.STATIC_HTTP: ladder.static_tier(client),
                                  AccessTier.RENDERED_BROWSER: ladder.rendered_tier(client, fake_render)})
        assert r.reasons[:2] == [FailureReason.ENCRYPTED_RESOURCE, FailureReason.JAVASCRIPT_REQUIRED]
        assert rendered == [u]                       # routed to a real browser...
        assert r.tier == AccessTier.RENDERED_BROWSER.value
        import sources.detect as d                   # ...and nothing here knows how to decrypt
        assert not any(hasattr(d, n) for n in ("decrypt", "aes_decrypt"))

    def test_bilibili_manga_shaped_empty_shell(self, isolated_db):
        u = "https://manga.bilibili.invalid/mc1/1"
        page = ('<html><head>' + '<script src="/a.js"></script>' * 10 + '</head><body>'
                '<div id="app"></div><noscript>We\'re sorry but this site doesn\'t work properly '
                'without JavaScript enabled. Please enable JavaScript to continue.</noscript>'
                + " " * 3000 + '</body></html>')
        r = self._static_only("bilibili_manga", {u: html(page)}, u)
        assert FailureReason.EMPTY_SPA_SHELL in r.reasons
        assert FailureReason.JAVASCRIPT_REQUIRED in r.reasons

    def test_newtoki_shaped_geo_restriction(self, isolated_db):
        u = "https://newtoki.invalid/webtoon/1"
        page = "<html><body><h1>해외 IP 차단</h1><p>해외에서는 접속하실 수 없습니다.</p></body></html>"
        r = self._static_only("newtoki", {u: html(page, 403)}, u)
        assert r.reasons[0] == FailureReason.GEO_RESTRICTION
        assert r.technical_status == TechnicalStatus.BLOCKED_IN_CURRENT_ENVIRONMENT.value


class TestCapabilitiesAndTestNow:
    def test_test_now_runs_only_the_chosen_tier(self, isolated_db):
        calls = []
        tiers = {
            AccessTier.STATIC_HTTP: _tier(False, [FailureReason.ACCESS_DENIED], log=calls,
                                          name="static"),
            AccessTier.RENDERED_BROWSER: _tier(True, html_text="<p>x</p>", log=calls,
                                               name="render"),
        }
        caps = ladder.test_tier("src", AccessTier.RENDERED_BROWSER, "https://x.invalid/",
                                tiers[AccessTier.RENDERED_BROWSER])
        assert calls == ["render"]
        assert caps.tiers["RENDERED_BROWSER"].tested and caps.tiers["RENDERED_BROWSER"].ok
        for other in ("STATIC_HTTP", "AUTHENTICATED_BROWSER", "USER_ASSISTED_BROWSER",
                      "OFFICIAL_API"):
            assert not caps.tiers[other].tested

        caps = ladder.test_tier("src", AccessTier.STATIC_HTTP, "https://x.invalid/",
                                tiers[AccessTier.STATIC_HTTP])
        assert calls == ["render", "static"]
        assert caps.tiers["STATIC_HTTP"].reason == "ACCESS_DENIED"
        assert caps.tiers["RENDERED_BROWSER"].ok        # earlier result untouched

    def test_test_now_recomputes_technical_status_on_success(self, isolated_db):
        # Step 86: a successful "Test Now" used to leave technical_status
        # exactly as it was -- "STATIC_HTTP OK but aggregate status still
        # UNRESOLVED".
        caps = ladder.test_tier("src86a", AccessTier.STATIC_HTTP, "https://x.invalid/",
                                _tier(True, html_text="<p>ok</p>"))
        assert caps.technical_status == TechnicalStatus.SUPPORTED.value
        assert caps.status == CapabilityStatus.VERIFIED.value

    def test_test_now_does_not_downgrade_status_on_a_failing_tier(self, isolated_db):
        # A single failing tier must never overwrite/downgrade whatever
        # a fuller, earlier ladder run already established.
        caps = ladder.test_tier("src86b", AccessTier.STATIC_HTTP, "https://x.invalid/",
                                _tier(True, html_text="<p>ok</p>"))
        assert caps.technical_status == TechnicalStatus.SUPPORTED.value
        caps = ladder.test_tier("src86b", AccessTier.AUTHENTICATED_BROWSER, "https://x.invalid/",
                                _tier(False, [FailureReason.AUTHENTICATION_REQUIRED]),
                                default=caps)
        assert caps.technical_status == TechnicalStatus.SUPPORTED.value

    def test_test_now_overwrites_an_untested_preset_access_method(self, isolated_db):
        # Step 86: several adapters preset a non-None access_method as
        # part of their built-in default (a declared expectation, never
        # itself tested) -- a real confirmed result must still be able
        # to overwrite that guess, not be permanently blocked by it.
        default = SourceCapabilities(platform="x", access_method=AccessTier.RENDERED_BROWSER.value)
        caps = ladder.test_tier("src86c", AccessTier.STATIC_HTTP, "https://x.invalid/",
                                _tier(True, html_text="<p>ok</p>"), default=default)
        assert caps.access_method == AccessTier.STATIC_HTTP.value

    def test_test_now_prefers_a_stronger_confirmed_tier_over_a_weaker_confirmed_one(self, isolated_db):
        caps = ladder.test_tier("src86d", AccessTier.RENDERED_BROWSER, "https://x.invalid/",
                                _tier(True, html_text="<p>ok</p>"))
        assert caps.access_method == AccessTier.RENDERED_BROWSER.value
        caps = ladder.test_tier("src86d", AccessTier.STATIC_HTTP, "https://x.invalid/",
                                _tier(True, html_text="<p>ok</p>"), default=caps)
        assert caps.access_method == AccessTier.STATIC_HTTP.value

    def test_test_now_keeps_a_stronger_confirmed_tier_over_a_later_weaker_one(self, isolated_db):
        caps = ladder.test_tier("src86e", AccessTier.STATIC_HTTP, "https://x.invalid/",
                                _tier(True, html_text="<p>ok</p>"))
        assert caps.access_method == AccessTier.STATIC_HTTP.value
        caps = ladder.test_tier("src86e", AccessTier.RENDERED_BROWSER, "https://x.invalid/",
                                _tier(True, html_text="<p>ok</p>"), default=caps)
        assert caps.access_method == AccessTier.STATIC_HTTP.value        # unchanged

    def test_technical_and_terms_stay_separate(self, isolated_db):
        from sources.models import SourceCapabilities
        caps = SourceCapabilities(platform="x", technical={"browser_accessible": True},
                                  terms={"read": "ToS §4", "tos_prohibited": False})
        ladder.apply_terms(caps)
        assert caps.technical_status != TechnicalStatus.DISQUALIFIED.value
        caps.terms["tos_prohibited"] = True
        ladder.apply_terms(caps)
        assert caps.technical_status == TechnicalStatus.DISQUALIFIED.value
        assert caps.technical == {"browser_accessible": True}
        roundtrip = SourceCapabilities.from_dict(caps.to_dict())
        assert roundtrip.terms == caps.terms and roundtrip.tiers.keys() == caps.tiers.keys()

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_stale_stored_record_cant_clear_a_corrected_built_in_prohibition(self, isolated_db):
        """Step 25q gap 1: an earlier import saved a stored record saying
        the source was fine. The adapter's own built-in default has since
        been corrected to tos_prohibited=True -- the stale stored `False`
        must not keep overriding it."""
        stored = SourceCapabilities(platform="src", terms={"tos_prohibited": False})
        ladder.save_capabilities("src", stored)
        default = SourceCapabilities(platform="src", terms={"tos_prohibited": True})
        with pytest.raises(TermsProhibited):
            ladder.check_terms("src", default)

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_stored_prohibition_survives_even_if_default_lacks_it(self, isolated_db):
        """The reverse must still hold: a stored record that itself
        recorded a prohibition isn't cleared just because the caller's
        `default` (e.g. a generic-import fallback) doesn't carry one."""
        stored = SourceCapabilities(platform="src", terms={"tos_prohibited": True})
        ladder.save_capabilities("src", stored)
        with pytest.raises(TermsProhibited):
            ladder.check_terms("src", None)


class TestStaticChecks:
    def test_every_http_call_in_sources_passes_a_timeout(self):
        """Same rule as tests/test_static_analysis.py's timeout checks,
        extended to the sources package: any .get/.post/.request call on
        requests or a session must carry timeout=."""
        import ast
        import os
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sources")
        problems = []
        for dirpath, _, files in os.walk(root):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dirpath, f)
                tree = ast.parse(open(path, encoding="utf-8").read(), path)
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr in ("get", "post", "request")
                            and isinstance(node.func.value, ast.Name)
                            and node.func.value.id in ("requests", "session")
                            and not any(kw.arg == "timeout" for kw in node.keywords)):
                        problems.append(f"{f}:{node.lineno}")
        assert problems == []


def _cookie_jar(**by_domain):
    """A real `requests.cookies.RequestsCookieJar`, not a plain dict standing
    in for one -- `by_domain` is {domain: {name: value}}. Used so these
    tests exercise the real library's own conflict/merge behavior instead
    of a fake that can't reproduce it."""
    from requests.cookies import RequestsCookieJar
    jar = RequestsCookieJar()
    for domain, cookies in by_domain.items():
        for name, value in cookies.items():
            jar.set(name, value, domain=domain, path="/")
    return jar


class TestRequestsTransport:
    """Step 25v bug 2, corrected after a real review round-trip: `requests`'
    own `Response.cookies` only carries the *final* hop's Set-Cookie
    headers -- a cookie set on an intermediate redirect hop is folded into
    the session's cookiejar but never copied onto the final response
    object. The first fix attempted here read from the shared thread-local
    session's own accumulated jar (`dict(session.cookies)`) instead of the
    per-call response chain -- that session is reused for every request any
    adapter makes on this thread for the app's whole runtime, so flattening
    it with `dict()` throws a real `requests.cookies.CookieConflictError`
    the moment two different hosts have ever set a same-named cookie (mangaz
    itself uses two hosts, `www.mangaz.com`/`vw.mangaz.com`, on one shared
    session). The real fix merges only this one call's own response chain
    (`r.history` + `r.cookies`), never the shared session jar. Tested here
    directly against a fake `requests.Session`/`Response` using **real**
    `RequestsCookieJar` objects (not plain dicts) for `.cookies`, so the
    real conflict/merge semantics are actually exercised, not stood in for."""

    @pytest.fixture(autouse=True)
    def _public_dns(self, monkeypatch):
        # B-25: the transport now validates each hop's host by resolving it;
        # the fake `.invalid` hosts don't resolve, so map them to a public IP.
        # The fake session below still hands back a response with `history`,
        # which the transport keeps merging (the cookie behaviour under test).
        monkeypatch.setattr("services.url_guard.resolve_public", lambda url: "93.184.216.34")

    class _FakeResp:
        def __init__(self, status_code=200, headers=None, content=b"", url="",
                     cookies=None, history=None):
            self.status_code = status_code
            self.headers = headers or {}
            self.content = content
            self.url = url
            self.cookies = cookies if cookies is not None else _cookie_jar()
            self.history = history or []
            self.raw = self._Raw(content)   # the transport streams (MED-1)

        class _Raw:
            def __init__(self, data):
                self.data = data

            def read1(self, n, decode_content=True):
                out, self.data = self.data[:n], self.data[n:]
                return out

        def close(self):
            pass

    class _FakeSession:
        def __init__(self, response, cookies=None):
            self._response = response
            # The shared session-wide jar `_thread_session()` would really
            # return -- deliberately seeded with a cross-host conflict so a
            # fix that reads from here (instead of the per-call response
            # chain) would blow up exactly like the real regression did.
            self.cookies = cookies if cookies is not None else _cookie_jar()

        def request(self, method, url, headers=None, data=None, timeout=None,
                    allow_redirects=True, proxies=None, stream=False):
            self.last_proxies = proxies
            return self._response

    def test_merges_cookies_from_a_redirect_hop_into_the_final_response(self, monkeypatch):
        hop = self._FakeResp(302, {"location": "/final"}, b"", "https://x.invalid/start",
                             cookies=_cookie_jar(**{"x.invalid": {"virgo!__ticket": "tick-1"}}))
        final = self._FakeResp(200, {}, b"ok", "https://x.invalid/final", history=[hop])
        monkeypatch.setattr("sources.http._thread_session", lambda: self._FakeSession(final))
        resp = _requests_transport("GET", "https://x.invalid/start", {}, None, 20)
        assert dict(resp.cookies) == {"virgo!__ticket": "tick-1"}

    def test_final_response_own_cookie_overrides_a_same_named_earlier_one(self, monkeypatch):
        hop = self._FakeResp(302, {}, b"", "https://x.invalid/start",
                             cookies=_cookie_jar(**{"x.invalid": {"session": "old"}}))
        final = self._FakeResp(200, {}, b"ok", "https://x.invalid/final",
                               cookies=_cookie_jar(**{"x.invalid": {"session": "new"}}),
                               history=[hop])
        monkeypatch.setattr("sources.http._thread_session", lambda: self._FakeSession(final))
        resp = _requests_transport("GET", "https://x.invalid/start", {}, None, 20)
        assert dict(resp.cookies) == {"session": "new"}

    def test_no_redirect_still_returns_the_final_responses_own_cookies(self, monkeypatch):
        final = self._FakeResp(200, {}, b"ok", "https://x.invalid/final",
                               cookies=_cookie_jar(**{"x.invalid": {"a": "b"}}))
        monkeypatch.setattr("sources.http._thread_session", lambda: self._FakeSession(final))
        resp = _requests_transport("GET", "https://x.invalid/final", {}, None, 20)
        assert dict(resp.cookies) == {"a": "b"}

    def test_no_cookie_conflict_error_across_a_cross_host_redirect(self, monkeypatch):
        """The exact shape of mangaz.com's own ticket flow: a redirect hop
        on one host sets a cookie with the same name a *different* host has
        already set on the shared session (e.g. `www.mangaz.com` and
        `vw.mangaz.com` both setting a `session` cookie). Reading from the
        shared session jar would raise CookieConflictError here; reading
        from just this call's own response chain must not."""
        hop = self._FakeResp(302, {}, b"", "https://vw.invalid/ticket",
                             cookies=_cookie_jar(**{"vw.invalid": {"session": "vw-value"}}))
        final = self._FakeResp(200, {}, b"ok", "https://vw.invalid/final", history=[hop])
        # The shared session has already accumulated a same-named cookie
        # from a wholly unrelated earlier request to a different host.
        shared_session_cookies = _cookie_jar(**{"www.invalid": {"session": "www-value"}})
        shared_session_cookies.update(hop.cookies)
        with pytest.raises(Exception) as exc_info:
            dict(shared_session_cookies)   # proves the rejected approach really does blow up
        assert "CookieConflictError" in type(exc_info.value).__name__

        monkeypatch.setattr("sources.http._thread_session",
                            lambda: self._FakeSession(final, cookies=shared_session_cookies))
        resp = _requests_transport("GET", "https://vw.invalid/ticket", {}, None, 20)
        assert dict(resp.cookies) == {"session": "vw-value"}

    def test_survives_an_empty_value_cookie(self, monkeypatch):
        """A real, live regression: kuaikan's own site sets a real cookie
        (`referer_name`) with an empty string value on every visit.
        `dict.update(jar)` (the merge shape this class replaced) calls the
        jar's own `__getitem__` per key, and some `requests` versions'
        `RequestsCookieJar._find_no_duplicates` treat a falsy cookie value
        as "not found" and raise `KeyError` instead of returning it (a real
        bug hit with `requests` 2.33.1; not reproducible with every
        installed version, so not asserted here -- only this fix's actual
        behavior is). Iterating each jar's own Cookie objects directly
        (this fix) never goes through that lookup path regardless."""
        final = self._FakeResp(200, {}, b"ok", "https://kuaikan.invalid/page",
                               cookies=_cookie_jar(**{"kuaikan.invalid": {"referer_name": ""}}))
        monkeypatch.setattr("sources.http._thread_session", lambda: self._FakeSession(final))
        resp = _requests_transport("GET", "https://kuaikan.invalid/page", {}, None, 20)
        assert dict(resp.cookies) == {"referer_name": ""}

    def test_no_proxy_by_default(self, monkeypatch, isolated_db):
        # Step 98: confirmed zero proxy support anywhere in the codebase
        # before this -- the default setting must keep every existing
        # direct-connection adapter unchanged.
        final = self._FakeResp(200, {}, b"ok", "https://x.invalid/")
        fake = self._FakeSession(final)
        monkeypatch.setattr("sources.http._thread_session", lambda: fake)
        _requests_transport("GET", "https://x.invalid/", {}, None, 20)
        assert fake.last_proxies is None

    def test_configured_proxy_is_applied_to_both_schemes(self, monkeypatch, isolated_db):
        store.set_setting("http_proxy_url", "http://127.0.0.1:8080")
        final = self._FakeResp(200, {}, b"ok", "https://x.invalid/")
        fake = self._FakeSession(final)
        monkeypatch.setattr("sources.http._thread_session", lambda: fake)
        _requests_transport("GET", "https://x.invalid/", {}, None, 20)
        assert fake.last_proxies == {"http": "http://127.0.0.1:8080",
                                     "https": "http://127.0.0.1:8080"}
