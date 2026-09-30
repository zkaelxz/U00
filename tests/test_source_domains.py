"""
tests/test_source_domains.py -- domain lists for sources that move
(sources/domains.py, services/source_domains_service.py and the PC-only
/api/source-domains routes). Scripted transport, a fake clock, and for the
redirect rules the real transport over a fake requests session; no
request reaches the network, and host names resolve through a fake
getaddrinfo.
"""
import json
import threading
import time

import pytest
import requests

from services import maintenance_assistant_service as assistant
from services import source_domains_service as svc
from services import url_guard
from sources import domains, health, store
from sources import http as src_http
from sources.adapters import toonkor, xbanxia
from sources.models import ChallengeDetected, FailureReason, FetchFailed, SourceUnavailable

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
TK = "https://toonkor0.org"
TK_NEW = "https://toonkor1.org"
XB = xbanxia.BASE_URL
XB_BARE = "https://xbanxia.cc"
PRIVATE_HOST = "private.example"

TOONKOR_HOME = ("<html><head><title>툰코 - 무료 웹툰</title></head><body>" + "".join(
    f"<div class='section-item-inner'><img src='/t{i}.jpg'><div class='section-item-title'>"
    f"<a href='/webtoon-{i}' alt='Webtoon {i}'>Webtoon {i}</a></div></div>" for i in range(4))
    + "</body></html>")
TOONKOR_SERIES = ("<html><body><table class='bt_view1'><tr><td class='bt_title'>Title</td>"
                  "</tr></table></body></html>")
XB_HOME = ("<html><head><title>半夏小說 - 網路小說線上免費看</title></head><body>"
           "<form action='/modules/article/search_t.php' method='post'>"
           "<input name='searchkey'><button name='Submit'>搜索</button></form>"
           + "".join(f"<a href='/books/{i}.html'>Book {i}</a>" for i in (11, 12, 13)) +
           "</body></html>")
XB_SERIES = ("<html><body><div class='book-describe'><h1>Novel</h1></div>"
             "<div class='book-list'><ul><li><a href='/books/2001/1.html'>第一章</a></li>"
             "</ul></div></body></html>")
XB_RESULTS = ("<html><body><ol><li class='pop-book2'><a href='/books/310978.html' title='T'>"
              "<h2 class='pop-tit'>T</h2></a></li></ol></body></html>")
CHALLENGE = html("<title>Just a moment...</title>", 403, {"cf-mitigated": "challenge"})


def _down():
    return requests.exceptions.ConnectionError(f"could not connect: {TK}/x?token={SECRET}")


@pytest.fixture
def world(isolated_db, monkeypatch):
    def fake_getaddrinfo(host, port, **kw):
        ip = "10.0.0.5" if host == PRIVATE_HOST else \
            host if host.replace(".", "").isdigit() else "93.184.216.34"
        return [(2, 1, 6, "", (ip, port))]

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", fake_getaddrinfo)
    src_http.reset_pacing_state()
    yield
    src_http.reset_pacing_state()


def _allow_discovery_again(source="toonkor"):
    store.set_setting(f"source_domain_discovery_at.{source}", None)


def _toonkor(routes, clock=None):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    return toonkor.ToonkorSource(client=make_client("toonkor", t, clock, max_retries=0)), t


def _xbanxia(routes, clock=None):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    return xbanxia.XbanxiaSource(client=make_client("xbanxia", t, clock, max_retries=0)), t


def _sources_db_dump() -> str:
    with store.connect() as conn:
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        rows = [dict(r) for t in tables for r in conn.execute(f"SELECT * FROM {t}")]
    return json.dumps(rows, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Failover and the persisted last good domain
# ---------------------------------------------------------------------------

class TestFailover:
    def test_tries_the_list_in_order_and_remembers_the_domain_that_worked(self, world):
        a, t = _xbanxia({f"{XB}/books/2001.html": _down(),
                         f"{XB_BARE}/books/2001.html": html(XB_SERIES)})
        info = a.get_series("2001")
        assert t.urls() == [f"{XB}/books/2001.html", f"{XB_BARE}/books/2001.html"]
        assert info.url == f"{XB_BARE}/books/2001.html"
        assert store.last_good_domain("xbanxia") == XB_BARE
        assert health.get("xbanxia")["consecutive_failures"] == 0

    def test_last_good_survives_a_new_client_and_process_state(self, world):
        store.set_last_good_domain("xbanxia", XB_BARE)
        src_http.reset_pacing_state()      # what a restart forgets
        a, t = _xbanxia({f"{XB_BARE}/books/2001.html": html(XB_SERIES)})
        assert a.base_url == XB_BARE
        a.get_series("2001")
        assert t.urls() == [f"{XB_BARE}/books/2001.html"]

    def test_a_saved_list_replaces_the_adapters_own(self, world):
        store.set_domain_list("toonkor", [TK_NEW, TK])
        a, t = _toonkor({f"{TK_NEW}/webtoon-1": html(TOONKOR_SERIES)})
        a.get_series("webtoon-1")
        assert t.urls() == [f"{TK_NEW}/webtoon-1"]

    def test_a_redirected_post_is_sent_again_to_the_final_listed_host(self, world):
        store.set_domain_list("xbanxia", [XB_BARE, XB])
        search = "/modules/article/search_t.php"
        # The bare domain's 301 turned the POST into a GET of the www page.
        a, t = _xbanxia({f"{XB_BARE}{search}": html("<html></html>", url=f"{XB}{search}"),
                         f"{XB}{search}": html(XB_RESULTS)})
        results = a.search("测试")
        assert [c["method"] for c in t.calls] == ["POST", "POST"]
        assert t.urls() == [f"{XB_BARE}{search}", f"{XB}{search}"]
        assert t.calls[1]["data"]["searchkey"] == "测试"
        assert [r.series_id for r in results] == ["310978"]
        assert results[0].url == f"{XB}/books/310978.html"
        assert store.last_good_domain("xbanxia") == XB

    def test_an_absolute_url_on_another_host_is_fetched_as_given(self, world):
        a, t = _toonkor({"https://elsewhere.example/c_1.html": html("<p>x</p>")})
        a._get("https://elsewhere.example/c_1.html", "x")
        assert t.urls() == ["https://elsewhere.example/c_1.html"]

    def test_a_404_does_not_fail_over(self, world):
        a, t = _xbanxia({})
        with pytest.raises(FetchFailed):
            a.get_series("2001")
        assert t.urls() == [f"{XB}/books/2001.html"]
        assert store.domain_proposals() == []


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_a_redirected_new_host_becomes_a_pending_proposal_only(self, world):
        a, t = _toonkor({
            # The old domain answers from a host that isn't on the list.
            f"{TK}/webtoon-1": html(TOONKOR_SERIES, url=f"{TK_NEW}/webtoon-1"),
            f"{TK}/": html("", 301, {"Location": f"{TK_NEW}/"}),
            f"{TK_NEW}/": html(TOONKOR_HOME),
        })
        with pytest.raises(SourceUnavailable) as e:
            a.get_series("webtoon-1")
        assert e.value.reason == FailureReason.ALL_DOMAINS_UNREACHABLE
        assert [(p["source"], p["host"]) for p in store.domain_proposals()] == \
            [("toonkor", "toonkor1.org")]
        # Pending only: the list is unchanged and nothing is fetched from it.
        assert {d["source"]: d["domains"] for d in svc.list_domains()}["toonkor"] == \
            ["toonkor0.org"]
        assert store.domain_list("toonkor") is None
        assert health.category(health.get("toonkor")["last_error_type"]) == "domains_unreachable"
        before = len(t.calls)
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert [u for u in t.urls()[before:] if u.startswith(TK_NEW)] == []

    def test_discovery_runs_at_most_once_per_interval(self, world):
        a, t = _toonkor({f"{TK}/webtoon-1": _down(), f"{TK}/": _down()})
        for _ in range(2):
            with pytest.raises(SourceUnavailable):
                a.get_series("webtoon-1")
        assert t.urls().count(f"{TK}/") == 1
        health.reset("toonkor")
        store.set_setting("source_domain_discovery_at.toonkor",
                          time.time() - domains.DISCOVERY_INTERVAL - 1)
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert t.urls().count(f"{TK}/") == 2

    def test_the_throttle_survives_a_new_client_and_process_state(self, world):
        routes = {f"{TK}/webtoon-1": _down(), f"{TK}/": _down()}
        a, t = _toonkor(dict(routes))
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert t.urls().count(f"{TK}/") == 1
        health.reset("toonkor")
        src_http.reset_pacing_state()      # what a restart forgets
        b, t2 = _toonkor(dict(routes))
        with pytest.raises(SourceUnavailable):
            b.get_series("webtoon-1")
        assert t2.urls() == [f"{TK}/webtoon-1"]

    def test_a_lookalike_is_not_proposed(self, world):
        lookalike = TOONKOR_HOME.replace("툰코", "웹툰천국")
        a, t = _toonkor({f"{TK}/webtoon-1": _down(),
                         f"{TK}/": html("", 302, {"Location": "https://toonkor-free.example/"}),
                         "https://toonkor-free.example/": html(lookalike)})
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert "https://toonkor-free.example/" in t.urls()
        assert store.domain_proposals() == []

    @pytest.mark.parametrize("location", [f"https://{PRIVATE_HOST}/", "https://10.0.0.7/",
                                          "http://toonkor2.org/"])
    def test_a_private_or_plain_http_candidate_is_refused(self, world, location):
        a, t = _toonkor({f"{TK}/webtoon-1": _down(),
                         f"{TK}/": html("", 301, {"Location": location}),
                         location: html(TOONKOR_HOME)})
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert store.domain_proposals() == []
        if location.startswith("http://"):
            assert location not in t.urls()   # never requested

    def test_too_many_redirects_end_the_search(self, world):
        hops = {f"https://hop{i}.example/": html("", 301, {"Location": f"https://hop{i + 1}.example/"})
                for i in range(domains.MAX_DISCOVERY_HOPS + 2)}
        a, t = _toonkor({f"{TK}/webtoon-1": _down(),
                         f"{TK}/": html("", 301, {"Location": "https://hop0.example/"}), **hops,
                         f"https://hop{domains.MAX_DISCOVERY_HOPS + 2}.example/": html(TOONKOR_HOME)})
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert store.domain_proposals() == []
        assert len([u for u in t.urls() if "hop" in u]) == domains.MAX_DISCOVERY_HOPS

    def test_a_challenge_hands_off_and_never_starts_discovery(self, world):
        store.set_domain_list("toonkor", [TK, TK_NEW])
        a, t = _toonkor({f"{TK}/webtoon-1": CHALLENGE, f"{TK}/": html(TOONKOR_HOME)})
        with pytest.raises(ChallengeDetected):
            a.get_series("webtoon-1")
        assert t.urls() == [f"{TK}/webtoon-1"]
        assert store.domain_proposals() == []


class TestVerifySite:
    def test_toonkor(self):
        assert toonkor.ToonkorSource.verify_site(TOONKOR_HOME)
        # The brand alone, or the markup alone, is not enough.
        assert not toonkor.ToonkorSource.verify_site(
            "<html><head><title>툰코</title></head><body><p>툰코 toonkor</p></body></html>")
        assert not toonkor.ToonkorSource.verify_site(TOONKOR_HOME.replace("툰코", "Webtoon Hub"))
        assert not toonkor.ToonkorSource.verify_site(TOONKOR_HOME.split("<div", 3)[0] +
                                                     "</body></html>")
        assert not toonkor.ToonkorSource.verify_site("")

    def test_xbanxia(self):
        assert xbanxia.XbanxiaSource.verify_site(XB_HOME)
        # Another jieqi-style novel site: same form and links, different name.
        assert not xbanxia.XbanxiaSource.verify_site(XB_HOME.replace("半夏小說", "筆趣閣"))
        assert not xbanxia.XbanxiaSource.verify_site(XB_HOME.replace("search_t.php", "search.php"))
        assert not xbanxia.XbanxiaSource.verify_site(
            XB_HOME.replace("/books/12.html", "/books/11.html").replace("/books/13.html",
                                                                        "/books/11.html"))
        assert not xbanxia.XbanxiaSource.verify_site("<title>半夏小說</title>")


# ---------------------------------------------------------------------------
# When nothing works: health and one backlog item
# ---------------------------------------------------------------------------

class TestUnreachable:
    def _fail(self, clock=None):
        a, t = _toonkor({f"{TK}/webtoon-1": _down(), f"{TK}/": _down()}, clock)
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        return t

    def test_one_backlog_item_per_source_until_it_is_cleared(self, world):
        assistant.set_settings({"developer_mode": True})
        self._fail()
        items = assistant.list_backlog()["items"]
        assert len(items) == 1
        text = items[0]["text"]
        assert text.startswith("[source-domains:toonkor]") and "toonkor0.org" in text
        assert SECRET not in text and "?" not in text and "://" not in text

        health.reset("toonkor")
        _allow_discovery_again()
        self._fail()
        assert len(assistant.list_backlog()["items"]) == 1

        assistant.delete_backlog_item(items[0]["id"], confirm=True)
        health.reset("toonkor")
        _allow_discovery_again()
        self._fail()
        assert len(assistant.list_backlog()["items"]) == 1

    def test_no_backlog_item_while_the_assistant_is_off(self, world):
        self._fail()
        assert assistant.list_backlog()["items"] == []

    def test_no_backlog_item_when_a_proposal_is_pending(self, world):
        assistant.set_settings({"developer_mode": True})
        store.propose_domain("toonkor", "toonkor1.org")
        self._fail()
        assert assistant.list_backlog()["items"] == []

    def test_nothing_stored_carries_a_secret_or_a_url_query(self, world):
        assistant.set_settings({"developer_mode": True})
        self._fail()
        dump = _sources_db_dump()
        assert SECRET not in dump and "token=" not in dump
        assert health.get("toonkor")["last_error_type"] == "ALL_DOMAINS_UNREACHABLE"


# ---------------------------------------------------------------------------
# PC-only routes
# ---------------------------------------------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


@pytest.fixture
def local(world):
    from fastapi.testclient import TestClient

    from api.api_config import ApiSettings
    from api.server import create_app
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


class TestRoutes:
    def test_list_shows_host_names_only(self, local):
        store.set_last_good_domain("xbanxia", XB_BARE)
        r = local.get("/api/source-domains")
        assert r.status_code == 200
        rows = {d["source"]: d for d in r.json()}
        assert set(rows) == {"toonkor", "xbanxia"}
        assert rows["xbanxia"] == {"source": "xbanxia", "display_name": "xbanxia.cc",
                                   "domains": ["www.xbanxia.cc", "xbanxia.cc"],
                                   "default_domains": ["www.xbanxia.cc", "xbanxia.cc"],
                                   "customized": False, "last_good": "xbanxia.cc",
                                   "pending_proposals": 0}
        assert "://" not in r.text

    def test_confirm_puts_the_host_first_and_the_next_request_uses_it(self, local):
        store.propose_domain("toonkor", "toonkor1.org", now=123.0)
        for source in ("toonkor", "toonkor"):
            health.record_failure(source, "ALL_DOMAINS_UNREACHABLE", "x", base_backoff=10)
        r = local.get("/api/source-domains/proposals")
        assert r.json() == [{"source": "toonkor", "display_name": "툰코 ToonKor",
                             "host": "toonkor1.org", "found_at": 123.0}]
        r = local.post("/api/source-domains/proposals/confirm",
                       json={"source": "toonkor", "host": "toonkor1.org"})
        assert r.status_code == 200, r.text
        assert r.json()["domains"] == ["toonkor1.org", "toonkor0.org"]
        assert r.json()["last_good"] == "toonkor1.org" and r.json()["customized"] is True
        assert local.get("/api/source-domains/proposals").json() == []
        assert health.get("toonkor")["consecutive_failures"] == 0
        a, t = _toonkor({f"{TK_NEW}/webtoon-1": html(TOONKOR_SERIES)})
        a.get_series("webtoon-1")
        assert t.urls() == [f"{TK_NEW}/webtoon-1"]
        # Confirmed once: a second confirm has nothing to confirm.
        assert local.post("/api/source-domains/proposals/confirm",
                          json={"source": "toonkor", "host": "toonkor1.org"}).status_code == 404

    def test_dismissed_hosts_are_not_proposed_again(self, local):
        store.propose_domain("toonkor", "toonkor2.org")
        r = local.post("/api/source-domains/proposals/dismiss",
                       json={"source": "toonkor", "host": "toonkor2.org"})
        assert r.status_code == 200 and r.json() == {"dismissed": True}
        assert store.propose_domain("toonkor", "toonkor2.org") is False
        assert local.get("/api/source-domains/proposals").json() == []
        assert store.domain_list("toonkor") is None

    def test_errors(self, local):
        ok = {"source": "toonkor", "host": "toonkor9.org"}
        assert local.post("/api/source-domains/proposals/confirm", json=ok).status_code == 404
        assert local.post("/api/source-domains/proposals/dismiss", json=ok).status_code == 404
        assert local.post("/api/source-domains/proposals/confirm",
                          json={"source": "baozimh", "host": "x.org"}).status_code == 404
        assert local.post("/api/source-domains/nosuch", json={"domains": ["a.org"]}).status_code == 404
        for bad in (["https://a.org/x?k=1"], ["10.0.0.1"], ["localhost"], ["[::1]"],
                    ["::ffff:10.0.0.1"], ["a.org:"], ["a.org:0"], ["a.org:65536"], ["a.org:abc"],
                    ["a.org:-1"], ["a.org:8443:1"], []):
            r = local.post("/api/source-domains/toonkor", json={"domains": bad})
            assert r.status_code == 422, bad
        assert store.domain_list("toonkor") is None

    def test_set_and_reset_a_list(self, local):
        r = local.post("/api/source-domains/xbanxia",
                       json={"domains": [" Mirror.Example. ", "www.xbanxia.cc", "mirror.example"]})
        assert r.status_code == 200, r.text
        assert r.json()["domains"] == ["mirror.example", "www.xbanxia.cc"]
        assert store.domain_list("xbanxia") == ["https://mirror.example", "https://www.xbanxia.cc"]
        r = local.post("/api/source-domains/xbanxia/reset")
        assert r.json()["domains"] == ["www.xbanxia.cc", "xbanxia.cc"]
        assert r.json()["customized"] is False

    def test_another_device_gets_nothing(self, world):
        from fastapi.testclient import TestClient

        from api import auth as api_auth
        from api.api_config import ApiSettings
        from api.server import create_app
        from services import auth_service
        store.propose_domain("toonkor", "toonkor1.org")
        user = auth_service.grant_admin_local("owner@example.com")
        s = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
        h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
             api_auth.CSRF_HEADER: s["csrf_token"]}
        for app in (create_app(ApiSettings(auth_mode="on")),):
            c = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
            assert c.get("/api/source-domains/proposals", headers=h).status_code == 403
            assert c.get("/api/source-domains", headers=h).status_code == 403
            assert c.post("/api/source-domains/proposals/confirm", headers=h,
                          json={"source": "toonkor", "host": "toonkor1.org"}).status_code == 403
            assert c.post("/api/source-domains/toonkor", headers=h,
                          json={"domains": ["a.org"]}).status_code == 403
        assert [p["host"] for p in store.domain_proposals()] == ["toonkor1.org"]


# ---------------------------------------------------------------------------
# Ports and trailing dots
# ---------------------------------------------------------------------------

class TestHostForms:
    def test_normalize_host(self):
        n = domains.normalize_host
        assert n("Toonkor1.ORG.") == "toonkor1.org"
        assert n("toonkor1.org:443") == "toonkor1.org"
        assert n("toonkor1.org:8443") == "toonkor1.org:8443"
        for bad in ("toonkor1.org:", "toonkor1.org:0", "toonkor1.org:65536", "toonkor1.org:x",
                    "toonkor1.org:８４４３", "example.com\n:8443", "example.com\n", "\nexample.com",
                    "exa mple.com", "example.com\t", "example.com\x00", "10.0.0.1", "[::1]", "::ffff:10.0.0.1", "https://a.org",
                    "a.org/x", "localhost", ""):
            assert n(bad) == "", bad

    def test_a_trailing_dot_proposal_is_stored_plain_and_can_be_confirmed(self, local):
        a, t = _toonkor({f"{TK}/webtoon-1": _down(),
                         f"{TK}/": html("", 301, {"Location": "https://toonkor1.org./"}),
                         "https://toonkor1.org./": html(TOONKOR_HOME)})
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert [p["host"] for p in store.domain_proposals()] == ["toonkor1.org"]
        r = local.post("/api/source-domains/proposals/confirm",
                       json={"source": "toonkor", "host": "toonkor1.org."})
        assert r.status_code == 200, r.text
        assert r.json()["domains"][0] == "toonkor1.org"

    def test_a_stored_trailing_dot_row_can_still_be_dismissed(self, local):
        store.propose_domain("toonkor", "toonkor3.org.")
        assert [p["host"] for p in local.get("/api/source-domains/proposals").json()] == \
            ["toonkor3.org"]
        r = local.post("/api/source-domains/proposals/dismiss",
                       json={"source": "toonkor", "host": "toonkor3.org"})
        assert r.status_code == 200 and store.domain_proposals() == []

    def test_a_non_default_port_is_kept_through_confirm(self, local):
        a, t = _toonkor({f"{TK}/webtoon-1": _down(),
                         f"{TK}/": html("", 301, {"Location": "https://toonkor1.org:8443/"}),
                         "https://toonkor1.org:8443/": html(TOONKOR_HOME)})
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert local.get("/api/source-domains/proposals").json()[0]["host"] == "toonkor1.org:8443"
        r = local.post("/api/source-domains/proposals/confirm",
                       json={"source": "toonkor", "host": "toonkor1.org:8443"})
        assert r.json()["domains"] == ["toonkor1.org:8443", "toonkor0.org"]
        assert store.domain_list("toonkor")[0] == "https://toonkor1.org:8443"
        b, t2 = _toonkor({"https://toonkor1.org:8443/webtoon-1": html(TOONKOR_SERIES)})
        b.get_series("webtoon-1")
        assert t2.urls() == ["https://toonkor1.org:8443/webtoon-1"]

    def test_an_invalid_port_in_a_redirect_is_refused(self, world):
        a, t = _toonkor({f"{TK}/webtoon-1": _down(),
                         f"{TK}/": html("", 301, {"Location": "https://toonkor1.org:99999/"}),
                         "https://toonkor1.org:99999/": html(TOONKOR_HOME)})
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert "https://toonkor1.org:99999/" not in t.urls()
        assert store.domain_proposals() == []

    def test_an_owner_can_enter_a_port(self, local):
        r = local.post("/api/source-domains/toonkor",
                       json={"domains": ["b.example:8443", "b.example:443"]})
        assert r.status_code == 200
        assert r.json()["domains"] == ["b.example:8443", "b.example"]


# ---------------------------------------------------------------------------
# The proposal cap and the throttle are atomic
# ---------------------------------------------------------------------------

class TestAtomic:
    def test_at_most_five_pending_proposals(self, world):
        results = [store.propose_domain("toonkor", f"t{i}.example") for i in range(7)]
        assert results == [True] * 5 + [False] * 2
        store.dismiss_domain_proposal("toonkor", "t0.example")
        assert store.propose_domain("toonkor", "t9.example") is True
        assert store.propose_domain("xbanxia", "x.example") is True   # per source

    def _race(self, fn, n=12):
        barrier = threading.Barrier(n)
        out = []

        def run(i):
            barrier.wait()
            out.append(fn(i))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(30)
        assert len(out) == n
        return out

    def test_concurrent_proposals_cannot_pass_the_cap(self, world):
        out = self._race(lambda i: store.propose_domain("toonkor", f"race{i}.example"))
        assert out.count(True) == store.MAX_PENDING_PROPOSALS
        assert len(store.domain_proposals("toonkor")) == store.MAX_PENDING_PROPOSALS

    def test_concurrent_discoveries_claim_once(self, world):
        out = self._race(lambda i: store.claim_discovery("toonkor", domains.DISCOVERY_INTERVAL))
        assert out.count(True) == 1


# ---------------------------------------------------------------------------
# Redirect rules through the real transport: a fake requests session under
# sources.http._requests_transport, so its own redirect code runs
# ---------------------------------------------------------------------------

class _Raw:
    def __init__(self, body: bytes):
        self.chunks = [body] if body else []

    def read1(self, n, decode_content=True):
        return self.chunks.pop(0) if self.chunks else b""


class _Resp:
    def __init__(self, url, status=200, body=b"", headers=None):
        self.url, self.status_code = url, status
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        self.raw, self.cookies, self.history = _Raw(body), [], []

    def close(self):
        pass


class _Session:
    def __init__(self, routes):
        self.routes, self.calls, self.sent = routes, [], []

    def request(self, method, url, **kw):
        assert kw.get("allow_redirects") is False and kw.get("timeout")
        self.calls.append(url)
        self.sent.append((method, url, kw.get("data")))
        route = self.routes.get(url)
        if isinstance(route, Exception):
            raise route
        if route is None:
            return _Resp(url, 404, b"<html>nf</html>")
        status, body, headers = route
        return _Resp(url, status, body.encode("utf-8"), headers)


def _real_adapter():
    return toonkor.ToonkorSource(client=make_client("toonkor", None, FakeClock(), max_retries=0))


@pytest.fixture
def real(world, monkeypatch):
    holder = {}
    monkeypatch.setattr(src_http, "_thread_session", lambda: holder["s"])

    def run(routes):
        holder["s"] = _Session(routes)
        with pytest.raises(SourceUnavailable):
            _real_adapter().get_series("webtoon-1")
        return holder["s"].calls

    run.holder = holder
    return run


def _redirect(to):
    return (301, "", {"Location": to})


class TestRealTransport:
    DOWN = {f"{TK}/webtoon-1": requests.exceptions.ConnectionError("down")}

    def test_a_proposal_is_found_hop_by_hop(self, real):
        calls = real({**self.DOWN, f"{TK}/": _redirect(f"{TK_NEW}/"),
                      f"{TK_NEW}/": (200, TOONKOR_HOME, {})})
        assert calls == [f"{TK}/webtoon-1", f"{TK}/", f"{TK_NEW}/"]
        assert [p["host"] for p in store.domain_proposals()] == ["toonkor1.org"]

    def test_a_downgrade_to_http_is_refused_and_never_requested(self, real):
        calls = real({**self.DOWN, f"{TK}/": _redirect("http://toonkor2.org/"),
                      "http://toonkor2.org/": (200, TOONKOR_HOME, {})})
        assert "http://toonkor2.org/" not in calls
        assert store.domain_proposals() == []

    def test_more_than_three_hops_are_refused(self, real):
        hops = {f"https://hop{i}.example/": _redirect(f"https://hop{i + 1}.example/")
                for i in range(6)}
        calls = real({**self.DOWN, f"{TK}/": _redirect("https://hop0.example/"), **hops,
                      "https://hop6.example/": (200, TOONKOR_HOME, {})})
        assert [c for c in calls if "hop" in c] == [f"https://hop{i}.example/" for i in range(3)]
        assert store.domain_proposals() == []

    def test_a_loop_is_refused(self, real):
        calls = real({**self.DOWN, f"{TK}/": _redirect("https://loop.example/"),
                      "https://loop.example/": _redirect(f"{TK}/")})
        assert calls.count(f"{TK}/") == 1 and calls.count("https://loop.example/") == 1
        assert store.domain_proposals() == []

    @pytest.mark.parametrize("target", [f"https://{PRIVATE_HOST}/", "https://10.0.0.7/",
                                        "https://[::ffff:10.0.0.1]/", "https://[fd00::1]/",
                                        "https://toonkor1.org:0/"])
    def test_an_unsafe_target_is_refused_and_never_requested(self, real, target):
        calls = real({**self.DOWN, f"{TK}/": _redirect(target), target: (200, TOONKOR_HOME, {})})
        assert calls == [f"{TK}/webtoon-1", f"{TK}/"]
        assert store.domain_proposals() == []

    def test_ordinary_fetches_still_follow_redirects(self, real):
        session = _Session({f"{TK}/webtoon-1": _redirect(f"{TK}/webtoon-1/"),
                            f"{TK}/webtoon-1/": (200, TOONKOR_SERIES, {})})
        real.holder["s"] = session
        assert _real_adapter().get_series("webtoon-1").title == "Title"
        assert session.calls == [f"{TK}/webtoon-1", f"{TK}/webtoon-1/"]


# ---------------------------------------------------------------------------
# The raw cache never holds an off-list host's page under a listed URL
# ---------------------------------------------------------------------------

class TestCache:
    def _adapter(self, routes):
        from sources.cache import RawCache
        a, t = _toonkor(routes)
        a.client.cache = RawCache("keep_originals")
        return a, t

    def test_an_off_list_page_is_not_cached_or_reused(self, world):
        routes = {f"{TK}/webtoon-1": html(TOONKOR_SERIES, url=f"{TK_NEW}/webtoon-1"),
                  f"{TK}/": _down()}
        a, t = self._adapter(dict(routes))
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert a.client.cache.get(f"{TK}/webtoon-1") is None
        health.reset("toonkor")
        a2, t2 = self._adapter(dict(routes))
        with pytest.raises(SourceUnavailable):
            a2.get_series("webtoon-1")
        assert t2.urls() == [f"{TK}/webtoon-1"]      # asked the network, not the cache

    def test_a_listed_page_is_still_cached(self, world):
        a, t = self._adapter({f"{TK}/webtoon-1": html(TOONKOR_SERIES)})
        a.get_series("webtoon-1")
        a2, t2 = self._adapter({})
        assert a2.get_series("webtoon-1").title == "Title"
        assert t2.urls() == []

    def test_same_site_rule(self):
        same = src_http._same_cache_site
        assert same("https://xbanxia.cc/b/1", "https://www.xbanxia.cc/b/1")
        assert same("https://www.xbanxia.cc/b/1", "https://xbanxia.cc/b/1")
        assert same("https://a.example/x", "https://A.example:443/y")
        for final in ("https://other.example/b/1", "https://xbanxia.cc:8443/b/1",
                      "http://xbanxia.cc/b/1", "https://xbanxia.cc./b/1",
                      "https://m.xbanxia.cc/b/1", "https://xbanxia.cc:bad/b/1"):
            assert not same("https://xbanxia.cc/b/1", final), final

    def test_a_bare_to_www_redirect_is_cached_again(self, world):
        from sources.cache import RawCache
        t = ScriptedTransport({"https://xbanxia.cc/b/1": html("<p>x</p>",
                                                              url="https://www.xbanxia.cc/b/1")})
        c = make_client("cachetest", t, FakeClock())
        c.cache = RawCache("keep_originals")
        c.get("https://xbanxia.cc/b/1")
        assert c.cache.get("https://xbanxia.cc/b/1") is not None

    def test_an_https_to_http_move_is_refused_and_not_cached(self, world):
        routes = {f"{TK}/webtoon-1": html(TOONKOR_SERIES, url="http://toonkor0.org/webtoon-1"),
                  f"{TK}/": _down()}
        a, t = self._adapter(routes)
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert a.client.cache.get(f"{TK}/webtoon-1") is None


# ---------------------------------------------------------------------------
# Robustness: malformed redirects, a locked database, the no-follow flag
# ---------------------------------------------------------------------------

class TestRobustness:
    @pytest.mark.parametrize("location", ["https://[oops/", "https://toonkor1.org:99x/", ""])
    def test_a_malformed_location_ends_the_search(self, world, location):
        a, t = _toonkor({f"{TK}/webtoon-1": _down(),
                         f"{TK}/": html("", 301, {"Location": location} if location else {})})
        with pytest.raises(SourceUnavailable):
            a.get_series("webtoon-1")
        assert store.domain_proposals() == []

    def test_a_locked_database_fails_closed(self, world, monkeypatch):
        import sqlite3
        monkeypatch.setattr(store, "BUSY_TIMEOUT", 0.1)
        store.connect().close()
        real_claim = store.claim_discovery
        raised = []

        def claim_while_another_writer_holds_the_lock(*args, **kw):
            holder = sqlite3.connect(store.db_path(), isolation_level=None)
            holder.execute("BEGIN IMMEDIATE")
            try:
                return real_claim(*args, **kw)
            except sqlite3.OperationalError:
                raised.append(True)
                raise
            finally:
                holder.execute("ROLLBACK")
                holder.close()

        monkeypatch.setattr(store, "claim_discovery", claim_while_another_writer_holds_the_lock)
        a, t = _toonkor({f"{TK}/webtoon-1": _down(), f"{TK}/": html(TOONKOR_HOME)})
        with pytest.raises(SourceUnavailable) as e:
            a.get_series("webtoon-1")
        assert raised == [True]
        assert e.value.reason == FailureReason.ALL_DOMAINS_UNREACHABLE
        assert f"{TK}/" not in t.urls()            # no probe without a claim
        assert health.get("toonkor")["last_error_type"] == "ALL_DOMAINS_UNREACHABLE"
        assert store.last_good_domain("toonkor") is None
        assert store.get_setting("source_domain_discovery_at.toonkor") is None

    def test_the_no_follow_flag_is_restored_after_an_error(self):
        assert not getattr(src_http._redirect_local, "off", False)
        with pytest.raises(RuntimeError):
            with src_http.redirects_not_followed():
                assert src_http._redirect_local.off is True
                raise RuntimeError("boom")
        assert src_http._redirect_local.off is False


# ---------------------------------------------------------------------------
# The search POST never carries its form off the list (real transport)
# ---------------------------------------------------------------------------

class TestPostRedirects:
    SEARCH = "/modules/article/search_t.php"

    def _search(self, monkeypatch, routes):
        session = _Session(routes)
        monkeypatch.setattr(src_http, "_thread_session", lambda: session)
        a = xbanxia.XbanxiaSource(client=make_client("xbanxia", None, FakeClock(), max_retries=0))
        return a, session

    def test_a_307_to_an_off_list_host_sends_no_form_there(self, world, monkeypatch):
        evil = "https://evil.example/collect"
        a, session = self._search(monkeypatch, {
            f"{XB}{self.SEARCH}": (307, "", {"Location": evil}),
            f"{XB_BARE}{self.SEARCH}": (200, XB_RESULTS, {}),
            evil: (200, XB_RESULTS, {})})
        results = a.search("测试")
        assert evil not in session.calls
        assert [(m, u) for m, u, _ in session.sent] == [("POST", f"{XB}{self.SEARCH}"),
                                                        ("POST", f"{XB_BARE}{self.SEARCH}")]
        assert [r.series_id for r in results] == ["310978"]

    def test_a_redirect_to_a_listed_host_resends_the_form_there(self, world, monkeypatch):
        store.set_domain_list("xbanxia", [XB_BARE, XB])
        a, session = self._search(monkeypatch, {
            f"{XB_BARE}{self.SEARCH}": (301, "", {"Location": f"{XB}{self.SEARCH}"}),
            f"{XB}{self.SEARCH}": (200, XB_RESULTS, {})})
        a.search("测试")
        assert [m for m, _, _ in session.sent] == ["POST", "POST"]
        assert session.sent[1][1] == f"{XB}{self.SEARCH}"
        assert session.sent[1][2]["searchkey"] == "测试"
        assert store.last_good_domain("xbanxia") == XB

    def test_a_redirect_to_http_on_a_listed_host_is_refused(self, world, monkeypatch):
        store.set_domain_list("xbanxia", [XB_BARE])
        a, session = self._search(monkeypatch, {
            f"{XB_BARE}{self.SEARCH}": (307, "", {"Location": f"http://www.xbanxia.cc{self.SEARCH}"}),
            f"{XB_BARE}/": _down()})
        with pytest.raises(SourceUnavailable):
            a.search("测试")
        assert not any(u.startswith("http://") for u in session.calls)


# ---------------------------------------------------------------------------
# Two real processes sharing one sources.db
# ---------------------------------------------------------------------------

def _two_process_worker(library_dir, tag, barrier, results):
    import db as child_db
    child_db.configure_library_dir(library_dir)
    from sources import store as child_store
    barrier.wait(60)
    proposed = [child_store.propose_domain("toonkor", f"{tag}{i}.example") for i in range(5)]
    claimed = child_store.claim_discovery("toonkor", 3600.0)
    results.put((tag, proposed.count(True), claimed))


class TestTwoProcesses:
    def test_cap_and_throttle_hold_across_processes(self, world):
        import multiprocessing
        import db
        store.connect().close()                    # schema exists before the race
        ctx = multiprocessing.get_context("spawn")
        barrier, results = ctx.Barrier(2), ctx.Queue()
        procs = [ctx.Process(target=_two_process_worker,
                             args=(db.LIBRARY_DIR, tag, barrier, results)) for tag in ("a", "b")]
        for p in procs:
            p.start()
        out = [results.get(timeout=120) for _ in procs]
        for p in procs:
            p.join(60)
            assert p.exitcode == 0
        assert sum(n for _, n, _ in out) == store.MAX_PENDING_PROPOSALS
        assert len(store.domain_proposals("toonkor")) == store.MAX_PENDING_PROPOSALS
        assert [c for _, _, c in out].count(True) == 1
