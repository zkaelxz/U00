"""
Tests for the optional web-search fallback (roadmap item 114,
services/web_search_service.py and api/routers/web_search_routes.py).
Fully mocked: a fake SearXNG stands in for requests.Session; no network.
"""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
requests = pytest.importorskip("requests")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app
from services import settings_service
from services import web_search_service as ws
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError)

URL = "http://192.168.1.30:8888"


class Resp:
    def __init__(self, status=200, data=None, raw=None):
        self.status_code = status
        self._body = raw if raw is not None else (json.dumps(data).encode() if data is not None
                                                  else b"")

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        pass


def _hit(n, url=None, **kw):
    return {"title": f"Result {n}", "content": f"Snippet {n}",
            "url": url or f"https://site{n}.example/page/{n}", **kw}


class FakeSearx:
    """Stands in for requests.Session: records every call, answers from
    `reply` (a Resp or a callable raising)."""
    calls = []
    reply = None

    def __init__(self):
        self.trust_env = True

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True,
            stream=False):
        FakeSearx.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout,
                                "allow_redirects": allow_redirects, "stream": stream,
                                "trust_env": self.trust_env})
        r = FakeSearx.reply
        return r() if callable(r) else r

    def close(self):
        pass


@pytest.fixture
def fake(isolated_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_service, "default_env_path", lambda: str(tmp_path / ".env"))
    monkeypatch.setattr(ws.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("192.168.1.30", port))])
    FakeSearx.calls, FakeSearx.reply = [], Resp(200, {"results": [_hit(1), _hit(2)]})
    monkeypatch.setattr(requests, "Session", FakeSearx)
    # Fresh buckets per test; the per-caller cap is raised here (most tests
    # search several times as one caller) and tested on its own below.
    monkeypatch.setattr(ws, "_rate", ws.SlidingWindowRateLimiter(ws.RATE_MAX, ws.RATE_WINDOW,
                                                                 max_keys=1))
    monkeypatch.setattr(ws, "_caller_rate", ws.SlidingWindowRateLimiter(100, ws.RATE_WINDOW))
    monkeypatch.setattr(ws, "_test_rate", ws.SlidingWindowRateLimiter(ws.TEST_RATE_MAX,
                                                                      ws.RATE_WINDOW, max_keys=1))
    return FakeSearx


def _on():
    ws.set_config(enabled=True, base_url=URL)


# --- settings and off by default ----------------------------------------------

def test_off_by_default(fake):
    assert ws.get_config() == {"enabled": False, "base_url": None}
    assert ws.status() == {"enabled": False}
    ws.set_config(base_url=URL)
    assert ws.status() == {"enabled": False}
    with pytest.raises(ConflictError):
        ws.search("女将军")
    assert fake.calls == []  # nothing is sent while off


def test_enabled_without_address_is_not_usable(fake):
    ws.set_config(enabled=True)
    assert ws.status() == {"enabled": False}
    with pytest.raises(InvalidInputError):
        ws.search("x")
    assert fake.calls == []


@pytest.mark.parametrize("bad", ["ftp://x.example", "http://user:pw@x.example",
                                 "http://x.example/?q=1", "http://x.example/#f", "notaurl",
                                 "javascript:alert(1)"])
def test_address_must_be_plain_http(fake, bad):
    with pytest.raises(InvalidInputError):
        ws.set_config(base_url=bad)
    assert ws.get_config()["base_url"] is None


def test_address_is_stored_without_trailing_slash_and_can_be_cleared(fake):
    ws.set_config(base_url="http://localhost:8888/searx/")
    assert ws.get_config()["base_url"] == "http://localhost:8888/searx"
    ws.set_config(base_url="")
    assert ws.get_config()["base_url"] is None


# --- the request -----------------------------------------------------------------

def test_search_queries_only_the_configured_host(fake):
    _on()
    out = ws.search("  The General  and the Princess ")
    call, = fake.calls
    assert call["url"] == URL + "/search"
    assert call["params"] == {"q": "The General and the Princess", "format": "json"}
    assert call["timeout"] == ws.HTTP_TIMEOUT
    assert call["allow_redirects"] is False and call["stream"] is True
    assert call["trust_env"] is False
    assert out["source"] == "searxng" and out["query"] == "The General and the Princess"


def test_results_are_labelled_links(fake):
    _on()
    fake.reply = Resp(200, {"results": [
        _hit(1, url="https://www.Example.com/a?b=1"),
        {"title": "", "content": None, "url": "http://no-title.example/x"},
        {"title": "bad scheme", "url": "javascript:alert(1)"},
        {"title": "file", "url": "file:///etc/passwd"},
        {"title": "creds", "url": "https://u:p@x.example/"},
        {"title": "ctrl", "url": "https://x.example/\nInjected"},
        "not a dict",
        _hit(1, url="https://www.Example.com/a?b=1"),  # duplicate
        {"title": "T\x00i\ttle", "content": "a" * 900, "url": "https://long.example/"},
    ]})
    rows = ws.search("x")["results"]
    assert rows[0] == {"title": "Result 1", "snippet": "Snippet 1",
                       "url": "https://www.Example.com/a?b=1", "domain": "www.example.com"}
    assert rows[1]["title"] == "no-title.example" and rows[1]["snippet"] == ""
    assert rows[2]["title"] == "T i tle" and len(rows[2]["snippet"]) == ws.MAX_SNIPPET_LEN
    assert len(rows) == 3


def test_result_count_is_capped(fake):
    _on()
    fake.reply = Resp(200, {"results": [_hit(i) for i in range(100)]})
    assert len(ws.search("x")["results"]) == ws.MAX_RESULTS == 20


def test_response_size_is_capped(fake, monkeypatch):
    _on()
    monkeypatch.setattr(ws, "MAX_RESPONSE_BYTES", 1000)
    fake.reply = Resp(200, {"results": [_hit(i) for i in range(100)]})
    with pytest.raises(DependencyUnavailableError):
        ws.search("x")


def test_slow_trickle_hits_the_read_deadline(fake, monkeypatch):
    _on()
    clock = iter([0.0, 1.0, ws.READ_DEADLINE + 1])
    monkeypatch.setattr(ws.time, "monotonic", lambda: next(clock))
    fake.reply = Resp(200, raw=b"x" * (3 * 64 * 1024))
    with pytest.raises(DependencyUnavailableError):
        ws.search("x")


def test_timeout_is_a_fixed_error(fake):
    _on()

    def boom():
        raise requests.Timeout(f"read timed out talking to {URL}")
    fake.reply = boom
    with pytest.raises(DependencyUnavailableError) as e:
        ws.search("x")
    assert URL not in str(e.value) and "192.168" not in str(e.value)


def test_redirect_is_not_followed(fake):
    _on()
    fake.reply = Resp(302)
    with pytest.raises(DependencyUnavailableError) as e:
        ws.search("x")
    assert "redirect" in str(e.value)
    assert len(fake.calls) == 1


def test_json_format_off_and_bad_replies(fake):
    _on()
    fake.reply = Resp(403)
    with pytest.raises(DependencyUnavailableError, match="JSON"):
        ws.search("x")
    fake.reply = Resp(429)
    with pytest.raises(DependencyUnavailableError, match="limiter"):
        ws.search("x")
    for reply in (Resp(500), Resp(200, raw=b"<html>"), Resp(200, raw=b"[1]"),
                  Resp(200, {"results": "nope"})):
        fake.reply = reply
        with pytest.raises(DependencyUnavailableError):
            ws.search("x")


def test_query_is_checked(fake):
    _on()
    for bad in ("", "   ", "\x00\x01", "x" * 201, None):
        with pytest.raises(InvalidInputError):
            ws.search(bad)
    assert fake.calls == []


def test_link_local_and_own_ports_refused(fake, monkeypatch):
    _on()
    monkeypatch.setattr(ws.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("169.254.169.254", port))])
    with pytest.raises(InvalidInputError):
        ws.search("x")
    ws.set_config(base_url="http://127.0.0.1:8600")
    monkeypatch.setattr(ws.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("127.0.0.1", port))])
    with pytest.raises(InvalidInputError):
        ws.search("x")
    assert fake.calls == []
    ws.set_config(base_url="http://127.0.0.1:8888")  # SearXNG on this PC is fine
    assert ws.search("x")["results"]


def test_household_port_refused_only_when_set(fake, monkeypatch):
    monkeypatch.setattr(ws.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("127.0.0.1", port))])
    monkeypatch.delenv(settings_service.HOUSEHOLD_PORT_ENV, raising=False)
    ws._check_target("http://127.0.0.1:8610")  # unset: an ordinary local port
    monkeypatch.setenv(settings_service.HOUSEHOLD_PORT_ENV, "8610")
    with pytest.raises(InvalidInputError):
        ws._check_target("http://127.0.0.1:8610")
    ws._check_target("http://127.0.0.1:8888")


def test_changed_api_port_is_refused_and_default_still_is(fake, monkeypatch):
    monkeypatch.setattr(ws.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("127.0.0.1", port))])
    monkeypatch.setenv(settings_service.API_PORT_ENV, "9123")
    for port in (9123, 8600, 8756):
        with pytest.raises(InvalidInputError):
            ws._check_target(f"http://127.0.0.1:{port}")
    ws._check_target("http://127.0.0.1:8888")


def test_one_search_at_a_time_per_caller(fake):
    _on()
    ws._in_flight.add("user:1")
    try:
        with pytest.raises(ConflictError):
            ws.search("x", caller="user:1")
        assert ws.search("x", caller="user:2")["results"]  # others are not blocked
    finally:
        ws._in_flight.discard("user:1")
    assert ws.search("x", caller="user:1")["results"]


def test_concurrent_searches_are_capped(fake):
    _on()
    for _ in range(ws.MAX_CONCURRENT):
        assert ws._slots.acquire(blocking=False)
    try:
        with pytest.raises(ConflictError):
            ws.search("x", caller="user:9")
    finally:
        for _ in range(ws.MAX_CONCURRENT):
            ws._slots.release()


def test_each_caller_has_its_own_bucket_inside_the_global_cap(fake, monkeypatch):
    from services.service_errors import RateLimitedError
    monkeypatch.setattr(ws, "_caller_rate", ws.SlidingWindowRateLimiter(ws.PER_CALLER_MAX,
                                                                        ws.RATE_WINDOW))
    _on()
    for _ in range(ws.PER_CALLER_MAX):
        ws.search("x", caller="user:1")
    with pytest.raises(RateLimitedError):
        ws.search("x", caller="user:1")
    # user:1 is capped, but the others still get through until the global cap.
    callers = ["user:2", "user:2", "user:3", "user:3", "user:3", "user:4"]
    for c in callers:
        ws.search("x", caller=c)
    assert len(fake.calls) == ws.RATE_MAX == ws.PER_CALLER_MAX + len(callers)
    with pytest.raises(RateLimitedError):
        ws.search("x", caller="user:5")
    assert len(fake.calls) == ws.RATE_MAX


def test_the_pc_test_is_not_blocked_by_searches(fake):
    from services.service_errors import RateLimitedError
    _on()
    for i in range(ws.RATE_MAX):
        ws.search("x", caller=f"user:{i}")
    with pytest.raises(RateLimitedError):
        ws.search("x", caller="user:99")
    ws._in_flight.add("local")
    for _ in range(ws.MAX_CONCURRENT):
        ws._slots.acquire(blocking=False)
    try:
        assert ws.test_connection()["ok"] is True
    finally:
        ws._in_flight.discard("local")
        for _ in range(ws.MAX_CONCURRENT):
            ws._slots.release()
    for _ in range(ws.TEST_RATE_MAX - 1):
        ws.test_connection()
    with pytest.raises(RateLimitedError):
        ws.test_connection()


def test_route_buckets_by_user_or_address(fake):
    from api.routers import web_search_routes as r

    class Req:
        def __init__(self, principal, host):
            self.state = type("S", (), {"principal": principal})()
            self.client = type("C", (), {"host": host})()
            self.headers = type("H", (), {"getlist": lambda self, k: []})()
    assert r._caller(Req({"user_id": 7}, "203.0.113.5")) == "user:7"
    assert r._caller(Req({"user_id": None}, "203.0.113.5")) == "ip:203.0.113.5"


def test_test_connection_works_while_off(fake):
    with pytest.raises(InvalidInputError):
        ws.test_connection()
    ws.set_config(base_url=URL)
    assert ws.test_connection() == {"ok": True, "result_count": 2}


def test_requests_have_timeouts():
    from tests.test_static_analysis import _find_requests_calls_missing_timeout
    path = ws.__file__
    assert _find_requests_calls_missing_timeout(path, session_verbs=True) == []


# --- routes -------------------------------------------------------------------------

@pytest.fixture
def client(fake):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def test_routes_round_trip(client):
    assert client.get("/api/web-search/status").json() == {"enabled": False}
    r = client.post("/api/web-search/search", json={"query": "x"})
    assert r.status_code == 409
    _on()
    assert client.get("/api/web-search/status").json() == {"enabled": True}
    assert URL not in client.get("/api/web-search/status").text
    r = client.post("/api/web-search/search", json={"query": "女将军和长公主"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "searxng" and body["results"][0]["domain"] == "site1.example"
    assert client.get("/api/web-search/config").json() == {"enabled": True, "base_url": URL}
    assert client.post("/api/web-search/test").json()["result_count"] == 2
    assert client.post("/api/web-search/config", json={"enabled": False}).json()["enabled"] is False


def test_routes_validation(client):
    _on()
    assert client.post("/api/web-search/search", json={}).status_code == 422
    assert client.post("/api/web-search/search", json={"query": "x" * 201}).status_code == 422
    assert client.post("/api/web-search/search",
                       json={"query": "x", "url": "http://evil.example"}).status_code == 422
    assert client.post("/api/web-search/config", json={"url": URL}).status_code == 422


def test_address_change_uses_the_endpoint_url_gate(fake):
    closed = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                        headers={"X-Baihe-Local": "1"})
    r = closed.post("/api/web-search/config", json={"base_url": URL, "confirm": True})
    assert r.status_code == 403 and ws.get_config()["base_url"] is None
    assert closed.post("/api/web-search/config", json={"enabled": True}).status_code == 200
    opened = TestClient(create_app(ApiSettings(allow_key_writes=True)),
                        raise_server_exceptions=False, headers={"X-Baihe-Local": "1"})
    assert opened.post("/api/web-search/config", json={"base_url": URL}).status_code == 422
    r = opened.post("/api/web-search/config", json={"base_url": URL, "confirm": True})
    assert r.status_code == 200 and r.json()["base_url"] == URL
    r = opened.post("/api/web-search/config", json={"base_url": "ftp://x", "confirm": True})
    assert r.status_code == 422


def test_settings_and_test_are_pc_only(fake):
    remote = TestClient(create_app(ApiSettings()), base_url="http://192.168.1.50:8600",
                        client=("192.168.1.50", 5000), raise_server_exceptions=False)
    for method, path in (("get", "/api/web-search/config"), ("post", "/api/web-search/config"),
                         ("post", "/api/web-search/test")):
        kwargs = {"json": {"enabled": True}} if method == "post" else {}
        assert getattr(remote, method)(path, **kwargs).status_code == 403, path
    assert ws.get_config()["enabled"] is False


# --- handing a result to the pasted-link flow ------------------------------------------

def test_a_web_result_goes_through_the_pasted_link_guards(fake):
    """A result pointing at a private address comes back as a link only; the
    pasted-link preview that the UI hands it to still refuses it."""
    from services import sources_url_service
    _on()
    fake.reply = Resp(200, {"results": [_hit(1, url="http://10.0.0.1/admin")]})
    row, = ws.search("x")["results"]
    assert row["url"] == "http://10.0.0.1/admin"
    assert len(fake.calls) == 1  # the result page itself was never fetched
    with pytest.raises(InvalidInputError):
        sources_url_service.start_preview(row["url"])
