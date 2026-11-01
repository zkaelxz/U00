"""
tests/test_source_domains.py -- domain lists for sources that move
(sources/domains.py, services/source_domains_service.py and the PC-only
/api/source-domains routes). Scripted transport and a fake clock; no
request reaches the network.
"""
import json

import pytest
import requests

from services import assistant_backlog_service as backlog, assistant_settings_service as settings_svc
from services import source_domains_service as svc
from sources import domains, health, store
from sources import http as src_http
from sources.adapters import xbanxia
from sources.cache import RawCache
from sources.models import ChallengeDetected, FailureReason, FetchFailed, SourceUnavailable

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
XB = xbanxia.BASE_URL
XB_BARE = "https://xbanxia.cc"
XB_NEW = "https://xbanxia1.org"
XB_PATH = "/books/2001.html"
SEARCH = "/modules/article/search_t.php"

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
    return requests.exceptions.ConnectionError(f"could not connect: {XB}/x?token={SECRET}")


def _moved(to, body=XB_HOME):
    """What the transport hands back after following a redirect to `to`."""
    return html(body, url=to)


@pytest.fixture
def world(isolated_db):
    src_http.reset_pacing_state()
    yield
    src_http.reset_pacing_state()


def _xbanxia(routes):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    return xbanxia.XbanxiaSource(client=make_client("xbanxia", t, clock, max_retries=0)), t


def _single(routes, cache=False):
    """xbanxia with a one-domain list, the shape a site with a single address has."""
    if store.domain_list("xbanxia") is None:
        store.set_domain_list("xbanxia", [XB])
    a, t = _xbanxia(routes)
    if cache:
        a.client.cache = RawCache("keep_originals")
    return a, t


def _hosts():
    return [p["host"] for p in store.domain_proposals()]


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
        store.set_domain_list("xbanxia", [XB_NEW, XB])
        a, t = _single({f"{XB_NEW}{XB_PATH}": html(XB_SERIES)})
        a.get_series("2001")
        assert t.urls() == [f"{XB_NEW}{XB_PATH}"]

    def test_a_search_that_ends_on_another_host_counts_as_failed(self, world):
        store.set_domain_list("xbanxia", [XB_BARE, XB])
        # The bare domain's 301 turned the POST into a GET of the www page.
        a, t = _xbanxia({f"{XB_BARE}{SEARCH}": html("<html></html>", url=f"{XB}{SEARCH}"),
                         f"{XB}{SEARCH}": html(XB_RESULTS)})
        results = a.search("测试")
        assert t.urls() == [f"{XB_BARE}{SEARCH}", f"{XB}{SEARCH}"]
        assert t.calls[1]["method"] == "POST" and t.calls[1]["data"]["searchkey"] == "测试"
        assert [r.series_id for r in results] == ["310978"]
        assert store.last_good_domain("xbanxia") == XB

    def test_an_absolute_url_on_another_host_is_fetched_as_given(self, world):
        a, t = _single({"https://elsewhere.example/c_1.html": html("<p>x</p>")})
        a._get("https://elsewhere.example/c_1.html", "x")
        assert t.urls() == ["https://elsewhere.example/c_1.html"]

    def test_a_404_does_not_fail_over(self, world):
        a, t = _xbanxia({})
        with pytest.raises(FetchFailed):
            a.get_series("2001")
        assert t.urls() == [f"{XB}/books/2001.html"]

    def test_a_challenge_hands_off_and_proposes_nothing(self, world):
        store.set_domain_list("xbanxia", [XB, XB_NEW])
        a, t = _single({f"{XB}{XB_PATH}": CHALLENGE})
        with pytest.raises(ChallengeDetected):
            a.get_series("2001")
        assert t.urls() == [f"{XB}{XB_PATH}"] and _hosts() == []


# ---------------------------------------------------------------------------
# Proposals from redirects
# ---------------------------------------------------------------------------

class TestProposals:
    def test_a_redirect_to_the_same_site_elsewhere_becomes_a_pending_proposal(self, world):
        a, t = _single({f"{XB}{XB_PATH}": _moved(f"{XB_NEW}/")})
        with pytest.raises(SourceUnavailable) as e:
            a.get_series("2001")
        assert e.value.reason == FailureReason.ALL_DOMAINS_UNREACHABLE
        assert "possible new address" in str(e.value)
        assert _hosts() == ["xbanxia1.org"]
        assert store.domain_list("xbanxia") == [XB]       # pending only
        assert t.urls() == [f"{XB}{XB_PATH}"]            # no extra request
        assert health.category(health.get("xbanxia")["last_error_type"]) == "domains_unreachable"

    def test_the_off_list_page_is_neither_used_nor_cached(self, world):
        a, t = _single({f"{XB}{XB_PATH}": _moved(f"{XB_NEW}{XB_PATH}", XB_SERIES)},
                        cache=True)
        with pytest.raises(SourceUnavailable):
            a.get_series("2001")
        assert a.client.cache.get(f"{XB}{XB_PATH}") is None
        health.reset("xbanxia")
        a2, t2 = _single({f"{XB}{XB_PATH}": _down()}, cache=True)
        with pytest.raises(SourceUnavailable):
            a2.get_series("2001")
        assert t2.urls() == [f"{XB}{XB_PATH}"]

    def test_a_listed_page_is_still_cached(self, world):
        a, _ = _single({f"{XB}{XB_PATH}": html(XB_SERIES)}, cache=True)
        a.get_series("2001")
        a2, t2 = _single({}, cache=True)
        assert a2.get_series("2001").title == "Novel" and t2.urls() == []

    @pytest.mark.parametrize("final", ["https://xbanxia-free.example/", "http://xbanxia2.org/",
                                       "http://www.xbanxia.cc/books/2001.html"])
    def test_a_lookalike_or_plain_http_result_is_not_proposed_or_used(self, world, final):
        body = XB_HOME.replace("半夏小說", "筆趣閣") if "free" in final else XB_HOME
        a, t = _single({f"{XB}{XB_PATH}": _moved(final, body)}, cache=True)
        with pytest.raises(SourceUnavailable):
            a.get_series("2001")
        assert _hosts() == [] and a.client.cache.get(f"{XB}{XB_PATH}") is None

    def test_host_forms_are_normalised(self, world):
        for final in ("https://Xbanxia1.ORG./", "https://xbanxia2.org:443/",
                      "https://xbanxia3.org:8443/"):
            a, _ = _single({f"{XB}{XB_PATH}": _moved(final)})
            with pytest.raises(SourceUnavailable):
                a.get_series("2001")
        assert sorted(_hosts()) == ["xbanxia1.org", "xbanxia2.org", "xbanxia3.org:8443"]

    def test_at_most_five_pending_and_dismissed_never_return(self, world):
        assert [store.propose_domain("xbanxia", f"t{i}.example") for i in range(7)] == \
            [True] * 5 + [False] * 2
        assert store.dismiss_domain_proposal("xbanxia", "t0.example")
        assert store.propose_domain("xbanxia", "t0.example") is False
        assert store.propose_domain("xbanxia", "t9.example") is True
        assert store.propose_domain("baozimh", "x.example") is True   # per source


class TestVerifySite:
    def test_xbanxia(self):
        assert xbanxia.XbanxiaSource.verify_site(XB_HOME)
        assert not xbanxia.XbanxiaSource.verify_site(XB_HOME.replace("半夏小說", "筆趣閣"))
        assert not xbanxia.XbanxiaSource.verify_site(XB_HOME.replace("search_t.php", "search.php"))
        assert not xbanxia.XbanxiaSource.verify_site("<title>半夏小說</title>")


class TestNormalizeHost:
    def test_forms(self):
        n = domains.normalize_host
        assert n("Xbanxia1.ORG.") == "xbanxia1.org"
        assert n(" xbanxia1.org:443 ") == "xbanxia1.org"
        assert n("xbanxia1.org:8443") == "xbanxia1.org:8443"
        for bad in ("xbanxia1.org:", "xbanxia1.org:0", "xbanxia1.org:65536", "xbanxia1.org:x",
                    "xbanxia1.org:８４４３", "example.com\n:8443", "exa mple.com", "example.com\t",
                    "10.0.0.1", "[::1]", "::ffff:10.0.0.1", "https://a.org", "a.org/x",
                    "localhost", ""):
            assert n(bad) == "", bad


# ---------------------------------------------------------------------------
# When nothing works: health and one backlog item
# ---------------------------------------------------------------------------

class TestUnreachable:
    def _fail(self):
        a, _ = _single({f"{XB}{XB_PATH}": _down()})
        with pytest.raises(SourceUnavailable):
            a.get_series("2001")
        health.reset("xbanxia")

    def test_one_backlog_item_per_source_until_it_is_cleared(self, world):
        settings_svc.set_settings({"developer_mode": True})
        self._fail()
        items = backlog.list_backlog()["items"]
        assert len(items) == 1
        text = items[0]["text"]
        assert text.startswith("[source-domains:xbanxia]") and "www.xbanxia.cc" in text
        assert SECRET not in text and "?" not in text and "://" not in text
        self._fail()
        assert len(backlog.list_backlog()["items"]) == 1
        backlog.delete_backlog_item(items[0]["id"], confirm=True)
        self._fail()
        assert len(backlog.list_backlog()["items"]) == 1

    def test_no_backlog_item_while_the_assistant_is_off_or_a_proposal_waits(self, world):
        self._fail()
        assert backlog.list_backlog()["items"] == []
        settings_svc.set_settings({"developer_mode": True})
        store.propose_domain("xbanxia", "xbanxia1.org")
        self._fail()
        assert backlog.list_backlog()["items"] == []

    def test_nothing_stored_carries_a_secret_or_a_url_query(self, world):
        settings_svc.set_settings({"developer_mode": True})
        self._fail()
        with store.connect() as conn:
            tables = [r["name"] for r in
                      conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            dump = json.dumps([dict(r) for t in tables for r in conn.execute(f"SELECT * FROM {t}")],
                              ensure_ascii=False, default=str)
        assert SECRET not in dump and "token=" not in dump


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
        rows = {d["source"]: d for d in r.json()}
        assert set(rows) == {"xbanxia"}
        assert rows["xbanxia"] == {"source": "xbanxia", "display_name": "xbanxia.cc",
                                   "domains": ["www.xbanxia.cc", "xbanxia.cc"],
                                   "default_domains": ["www.xbanxia.cc", "xbanxia.cc"],
                                   "customized": False, "last_good": "xbanxia.cc",
                                   "pending_proposals": 0}
        assert "://" not in r.text

    def test_confirm_puts_the_host_first_and_the_next_request_uses_it(self, local):
        store.propose_domain("xbanxia", "xbanxia1.org:8443", now=123.0)
        health.record_failure("xbanxia", "ALL_DOMAINS_UNREACHABLE", "x", base_backoff=10)
        assert local.get("/api/source-domains/proposals").json() == [
            {"source": "xbanxia", "display_name": "xbanxia.cc", "host": "xbanxia1.org:8443",
             "found_at": 123.0}]
        r = local.post("/api/source-domains/proposals/confirm",
                       json={"source": "xbanxia", "host": "xbanxia1.org:8443"})
        assert r.status_code == 200, r.text
        assert r.json()["domains"] == ["xbanxia1.org:8443", "www.xbanxia.cc", "xbanxia.cc"]
        assert r.json()["last_good"] == "xbanxia1.org:8443" and r.json()["customized"] is True
        assert local.get("/api/source-domains/proposals").json() == []
        assert health.get("xbanxia")["consecutive_failures"] == 0
        a, t = _single({"https://xbanxia1.org:8443/books/2001.html": html(XB_SERIES)})
        a.get_series("2001")
        assert t.urls() == ["https://xbanxia1.org:8443/books/2001.html"]
        assert local.post("/api/source-domains/proposals/confirm",
                          json={"source": "xbanxia", "host": "xbanxia1.org:8443"}).status_code == 404

    def test_trailing_dot_confirm_and_dismiss(self, local):
        store.propose_domain("xbanxia", "xbanxia1.org")
        store.propose_domain("xbanxia", "xbanxia3.org.")    # a row stored with the dot
        r = local.post("/api/source-domains/proposals/confirm",
                       json={"source": "xbanxia", "host": "xbanxia1.org."})
        assert r.status_code == 200 and r.json()["domains"][0] == "xbanxia1.org"
        r = local.post("/api/source-domains/proposals/dismiss",
                       json={"source": "xbanxia", "host": "xbanxia3.org"})
        assert r.status_code == 200 and r.json() == {"dismissed": True}
        assert store.domain_proposals() == []

    def test_errors(self, local):
        ok = {"source": "xbanxia", "host": "xbanxia9.org"}
        assert local.post("/api/source-domains/proposals/confirm", json=ok).status_code == 404
        assert local.post("/api/source-domains/proposals/dismiss", json=ok).status_code == 404
        assert local.post("/api/source-domains/proposals/confirm",
                          json={"source": "baozimh", "host": "x.org"}).status_code == 404
        assert local.post("/api/source-domains/nosuch", json={"domains": ["a.org"]}).status_code == 404
        for bad in (["https://a.org/x?k=1"], ["10.0.0.1"], ["localhost"], ["[::1]"],
                    ["a.org:"], ["a.org:0"], ["a.org:65536"], ["a.org:abc"], ["a.org\n:1"], []):
            r = local.post("/api/source-domains/xbanxia", json={"domains": bad})
            assert r.status_code == 422, bad
        assert store.domain_list("xbanxia") is None

    def test_set_and_reset_a_list(self, local):
        r = local.post("/api/source-domains/xbanxia",
                       json={"domains": [" Mirror.Example. ", "www.xbanxia.cc", "mirror.example",
                                         "b.example:8443"]})
        assert r.status_code == 200, r.text
        assert r.json()["domains"] == ["mirror.example", "www.xbanxia.cc", "b.example:8443"]
        r = local.post("/api/source-domains/xbanxia/reset")
        assert r.json()["domains"] == ["www.xbanxia.cc", "xbanxia.cc"]
        assert r.json()["customized"] is False

    def test_another_device_gets_nothing(self, world):
        from fastapi.testclient import TestClient

        from api import auth as api_auth
        from api.api_config import ApiSettings
        from api.server import create_app
        from services import auth_service
        store.propose_domain("xbanxia", "xbanxia1.org")
        user = auth_service.grant_admin_local("owner@example.com")
        s = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
        h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
             api_auth.CSRF_HEADER: s["csrf_token"]}
        c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                       raise_server_exceptions=False)
        assert c.get("/api/source-domains/proposals", headers=h).status_code == 403
        assert c.get("/api/source-domains", headers=h).status_code == 403
        assert c.post("/api/source-domains/proposals/confirm", headers=h,
                      json={"source": "xbanxia", "host": "xbanxia1.org"}).status_code == 403
        assert c.post("/api/source-domains/xbanxia", headers=h,
                      json={"domains": ["a.org"]}).status_code == 403
        assert _hosts() == ["xbanxia1.org"]
        assert svc.list_proposals()[0]["host"] == "xbanxia1.org"
