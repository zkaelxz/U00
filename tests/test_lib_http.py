"""lib/http.py: guard on every hop, caps, deadline, fixed-text errors. No network."""
import pytest

from lib import http
from lib.errors import InvalidInputError


class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self.headers, self._body = status, headers or {}, body
        self.encoding, self.closed = None, False

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


def _serve(monkeypatch, *responses):
    it, sent = iter(responses), []

    def fake(url, ip, headers, timeout=None, method="GET", **kw):
        sent.append((url, ip, method))
        return next(it)
    monkeypatch.setattr(http, "pinned_get", fake)
    return sent


def _get(url="http://a.example/", **kw):
    kw.setdefault("guard", lambda u: "1.2.3.4")
    return http.get(url, timeout=5, max_bytes=100, **kw)


def test_every_redirect_hop_goes_through_the_guard(monkeypatch):
    sent = _serve(monkeypatch, Resp(302, headers={"Location": "/b"}), Resp(200, b"ok"))
    seen = []
    resp = _get(guard=lambda u: seen.append(u) or "1.2.3.4")
    assert seen == ["http://a.example/", "http://a.example/b"] and resp.body == b"ok"
    assert resp.url == "http://a.example/b" and [s[1] for s in sent] == ["1.2.3.4"] * 2


def test_a_guard_refusal_on_a_later_hop_stops_before_connecting(monkeypatch):
    sent = _serve(monkeypatch, Resp(302, headers={"Location": "http://127.0.0.1/"}))

    def guard(url):
        if "127.0.0.1" in url:
            raise InvalidInputError("not public")
        return "1.2.3.4"
    with pytest.raises(InvalidInputError):
        _get(guard=guard)
    assert len(sent) == 1


def test_too_many_redirects_and_unfollowed_redirects(monkeypatch):
    _serve(monkeypatch, *[Resp(302, headers={"Location": "/n"}) for _ in range(9)])
    with pytest.raises(http.FetchError) as ei:
        _get()
    assert ei.value.message == http.FETCH_FAILED
    _serve(monkeypatch, Resp(302, headers={"Location": "/n"}))
    assert _get(allow_redirects=False).status == 302


def test_an_oversized_body_is_refused_or_cut(monkeypatch):
    _serve(monkeypatch, Resp(body=b"x" * 500))
    with pytest.raises(http.ResponseTooLarge):
        _get()
    resp = Resp(body=b"x" * 500)
    _serve(monkeypatch, resp)
    assert len(_get(truncate=True).body) == 100 and resp.closed


def test_the_total_deadline_covers_a_dripping_body(monkeypatch):
    now = {"t": 0.0}

    def slow():
        now["t"] += 10
        return now["t"]
    _serve(monkeypatch, Resp(body=b"x" * 50))
    with pytest.raises(http.ResponseTooSlow):
        http.get("http://a.example/", timeout=5, max_bytes=100, deadline=15,
                 guard=None, clock=slow)


def test_a_transport_error_is_fixed_text(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("failed http://secret.example/?key=abc")
    monkeypatch.setattr(http, "pinned_get", boom)
    with pytest.raises(http.FetchError) as ei:
        _get()
    assert ei.value.message == http.FETCH_FAILED and ei.value.__cause__ is None


def test_check_public_refuses_private_hosts(monkeypatch):
    monkeypatch.setattr(http.url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("10.0.0.1", port))])
    with pytest.raises(InvalidInputError):
        http.check_public("http://a.example/")


def _serve_headers(monkeypatch, *responses):
    it, seen = iter(responses), []

    def fake(url, ip, headers, timeout=None, method="GET", **kw):
        seen.append((url, headers))
        return next(it)
    monkeypatch.setattr(http, "pinned_get", fake)
    return seen


@pytest.mark.parametrize("target", [
    "http://attacker.example/", "https://other.example/", "https://a.example:8443/",
    "http://a.example/"])  # https -> http on the same host
def test_credentials_are_dropped_when_a_redirect_changes_origin(monkeypatch, target):
    seen = _serve_headers(monkeypatch, Resp(307, headers={"Location": target}), Resp(200, b"ok"))
    http.get("https://a.example/", timeout=5, max_bytes=100, guard=lambda u: None,
             allow_redirects=True,
             headers={"Authorization": "Bearer k", "x-goog-api-key": "k", "Accept": "a/b"})
    assert seen[0][1]["Authorization"] == "Bearer k"
    assert seen[1][1] == {"Accept": "a/b"}


def test_credentials_stay_on_a_same_origin_redirect(monkeypatch):
    seen = _serve_headers(monkeypatch, Resp(302, headers={"Location": "/b"}), Resp(200, b"ok"))
    http.get("https://a.example/", timeout=5, max_bytes=100, headers={"Authorization": "k"},
             guard=lambda u: None)
    assert seen[1][1] == {"Authorization": "k"}


def test_redirects_are_not_followed_without_a_guard(monkeypatch):
    seen = _serve_headers(monkeypatch, Resp(307, headers={"Location": "http://127.0.0.1/"}))
    resp = http.post("https://api.example/", timeout=5, max_bytes=100, guard=None,
                     headers={"Authorization": "Bearer k"})
    assert resp.status == 307 and len(seen) == 1


def test_an_error_body_is_cut_to_max_error_bytes_and_keeps_its_status(monkeypatch):
    _serve(monkeypatch, Resp(401, b"x" * 500))
    resp = _get(max_error_bytes=10)
    assert resp.status == 401 and resp.body == b"x" * 10


def test_an_unknown_charset_falls_back_to_utf8():
    r = http.Response(200, {}, "é".encode(), "http://a/", encoding="bogus-charset")
    assert r.text() == "é"


def test_a_connect_read_timeout_pair_is_passed_on_and_bounded_by_the_deadline(monkeypatch):
    seen = []

    def fake(url, ip, headers, timeout=None, method="GET", **kw):
        seen.append((timeout, kw))
        return Resp(200, b"{}")
    monkeypatch.setattr(http, "pinned_get", fake)
    http.get("http://a.example/", timeout=(3.05, 20), max_bytes=100, guard=None, deadline=10)
    connect, read = seen[0][0]
    assert connect == 3.05 and 9 < read <= 10


def test_trust_env_is_only_passed_when_turned_off(monkeypatch):
    seen = []

    def fake(url, ip, headers, timeout=None, method="GET", **kw):
        seen.append(kw)
        return Resp(200, b"{}")
    monkeypatch.setattr(http, "pinned_get", fake)
    http.get("http://a.example/", timeout=5, max_bytes=100, guard=None)
    http.get("http://a.example/", timeout=5, max_bytes=100, guard=None, trust_env=False)
    assert seen == [{}, {"trust_env": False}]


# --- session(): the same rules for callers that need a real requests.Session ---

class _FakeAdapter:
    """Stands in for the network: answers each request from `plan` and records
    what the session sent."""

    def __init__(self, *plan):
        self.plan, self.sent = list(plan), []

    def send(self, request, **kw):
        import io
        import requests
        from urllib3.response import HTTPResponse
        status, body, headers = self.plan.pop(0)
        self.sent.append((request.url, kw, dict(request.headers)))
        resp = requests.Response()
        resp.status_code, resp.url, resp.request = status, request.url, request
        resp.headers.update(headers)
        resp.raw = HTTPResponse(body=io.BytesIO(body), preload_content=False, status=status)
        return resp

    def close(self):
        pass


def _session(*plan, **kw):
    kw.setdefault("guard", lambda u: None)
    kw.setdefault("timeout", 7)
    kw.setdefault("max_bytes", 100)
    s = http.session(**kw)
    adapter = _FakeAdapter(*plan)
    s.mount("http://", adapter)
    return s, adapter


def test_session_redirect_to_a_private_address_is_refused():
    def guard(url):
        if "10.0.0.1" in url:
            raise InvalidInputError("private")
        return "1.2.3.4"
    s, adapter = _session((302, b"", {"Location": "http://10.0.0.1/x"}), guard=guard)
    with pytest.raises(InvalidInputError):
        s.get("http://a.example/")
    assert [u for u, _, _ in adapter.sent] == ["http://a.example/"]  # the private hop was never sent


def test_session_default_guard_refuses_a_loopback_url():
    s = http.session(timeout=5, max_bytes=10)
    with pytest.raises(InvalidInputError):
        s.get("http://127.0.0.1/")


def test_session_guards_every_hop_and_a_bare_send():
    seen = []
    s, adapter = _session((302, b"", {"Location": "/b"}), (200, b"ok", {}),
                          guard=lambda u: seen.append(u))
    assert s.get("http://a.example/").content == b"ok"
    assert seen == ["http://a.example/", "http://a.example/b"]
    s.mount("http://", _FakeAdapter((200, b"", {})))
    s.send(s.prepare_request(__import__("requests").Request("GET", "http://c.example/")))
    assert seen[-1] == "http://c.example/"


def test_session_always_sets_a_timeout():
    s, adapter = _session((200, b"", {}), (200, b"", {}), (200, b"", {}))
    s.get("http://a.example/")
    s.get("http://a.example/", timeout=None)
    s.get("http://a.example/", timeout=(1, 2))
    assert [kw["timeout"] for _, kw, _ in adapter.sent] == [7, 7, (1, 2)]


def test_session_does_not_follow_redirects_without_a_guard_unless_asked():
    s, adapter = _session((302, b"", {"Location": "/b"}), guard=None)
    assert s.get("http://a.example/").status_code == 302 and len(adapter.sent) == 1
    s, adapter = _session((302, b"", {"Location": "/b"}), (200, b"", {}), guard=None)
    assert s.get("http://a.example/", allow_redirects=True).status_code == 200


def test_session_stops_after_too_many_redirects():
    hop = (302, b"", {"Location": "/again"})
    s, _ = _session(*[hop] * 5, max_redirects=2)
    with pytest.raises(http.FetchError):
        s.get("http://a.example/", allow_redirects=True)


def test_session_credential_headers_do_not_follow_a_cross_origin_redirect():
    s, adapter = _session((302, b"", {"Location": "http://b.example/"}), (200, b"", {}))
    s.get("http://a.example/", headers={"Authorization": "x", "X-Plain": "1"}, allow_redirects=True)
    first, second = adapter.sent[0][2], adapter.sent[1][2]
    assert "Authorization" in first and "Authorization" not in second and second["X-Plain"] == "1"


def test_session_cap_is_enforced_on_a_streamed_body_however_it_is_read():
    for read in (lambda r: r.content, lambda r: list(r.iter_content(10)),
                 lambda r: r.raw.read(1000), lambda r: r.raw.read1(1000)):
        s, _ = _session((200, b"x" * 101, {}))
        with pytest.raises(http.ResponseTooLarge):
            read(s.get("http://a.example/"))
    s, _ = _session((200, b"x" * 100, {}))
    assert s.get("http://a.example/").content == b"x" * 100


def test_session_cap_can_change_between_requests():
    s, _ = _session((200, b"x" * 50, {}))
    s.max_bytes = 10
    with pytest.raises(http.ResponseTooLarge):
        s.get("http://a.example/").content


def test_session_refuses_a_custom_adapter_with_a_guard():
    with pytest.raises(ValueError):
        http.session(timeout=5, max_bytes=1, guard=lambda u: "1.2.3.4", adapter=object())
