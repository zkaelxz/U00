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
