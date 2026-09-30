"""Slice 54 (D-0): services/safe_fetch.py. Mocked only; no network."""
import io

import pytest

from services import metadata_service, safe_fetch
from services.service_errors import DependencyUnavailableError, InvalidInputError

pytest.importorskip("bs4")

PUBLIC = "93.184.216.34"
BODY = "<html><body><p>" + ("Hello readable page text. " * 30) + "</p></body></html>"


def _dns(monkeypatch, mapping=None, default=PUBLIC):
    def fake(host, port, **kw):
        return [(2, 1, 6, "", ((mapping or {}).get(host, default), port))]
    monkeypatch.setattr(metadata_service.socket, "getaddrinfo", fake)


class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.encoding = "utf-8"
        self.reads = 0
        self.closed = False
        buf = io.BytesIO(body)

        class Raw:
            def read(_, n, decode_content=False):
                self.reads += n
                return buf.read(n)
        self.raw = Raw()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"boom http://secret.example/x?key=abc {self.status_code}")

    def close(self):
        self.closed = True


def _patch_get(monkeypatch, responses, calls=None):
    it = iter(responses)

    def fake(url, ip, headers):
        if calls is not None:
            calls.append((url, ip))
        return next(it)
    monkeypatch.setattr(metadata_service, "_pinned_get", fake)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.5", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "240.0.0.1", "::1", "fe80::1", "fc00::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
])
def test_private_ips_are_422_without_connecting(monkeypatch, ip):
    _dns(monkeypatch, default=ip)
    calls = []
    _patch_get(monkeypatch, [], calls)
    with pytest.raises(InvalidInputError) as ei:
        safe_fetch.fetch_public_text("http://evil.example/page")
    assert calls == []
    assert "evil.example" not in ei.value.message


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://a.example/", "http://u:p@a.example/",
                                 "not a url", ""])
def test_bad_urls_rejected(monkeypatch, url):
    _dns(monkeypatch)
    calls = []
    _patch_get(monkeypatch, [], calls)
    with pytest.raises(InvalidInputError):
        safe_fetch.fetch_public_text(url)
    assert calls == []


def test_success_returns_text(monkeypatch):
    _dns(monkeypatch)
    _patch_get(monkeypatch, [Resp(200, BODY.encode())])
    res = safe_fetch.fetch_public_text("http://ok.example/")
    assert not res.needs_manual and "Hello readable page text." in res.text


def test_redirect_to_private_host_blocked(monkeypatch):
    _dns(monkeypatch, {"internal.example": "127.0.0.1"})
    calls = []
    _patch_get(monkeypatch, [Resp(302, headers={"Location": "http://internal.example/admin"})],
               calls)
    with pytest.raises(InvalidInputError):
        safe_fetch.fetch_public_text("http://ok.example/")
    assert calls == [("http://ok.example/", PUBLIC)]


def test_each_hop_pinned_to_its_own_validated_ip(monkeypatch):
    _dns(monkeypatch, {"a.example": "93.184.216.34", "b.example": "8.8.8.8"})
    calls = []
    _patch_get(monkeypatch, [Resp(301, headers={"Location": "http://b.example/n"}),
                             Resp(200, BODY.encode())], calls)
    safe_fetch.fetch_public_text("http://a.example/")
    assert calls == [("http://a.example/", "93.184.216.34"), ("http://b.example/n", "8.8.8.8")]


def test_real_pinned_get_connects_to_validated_ip(monkeypatch):
    import requests
    answers = iter([PUBLIC, "127.0.0.1"])
    monkeypatch.setattr(metadata_service.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", (next(answers), port))])
    sent = []

    def fake_send(self, request, **kw):
        sent.append(request.url)
        r = requests.Response()
        r.status_code = 200
        buf = io.BytesIO(BODY.encode())

        class Raw:
            def read(_, n, decode_content=False):
                return buf.read(n)

            def close(_):
                pass
            release_conn = close
        r.raw = Raw()
        return r
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    safe_fetch.fetch_public_text("http://ok.example/p")
    assert sent == [f"http://{PUBLIC}/p"]


def test_hop_cap(monkeypatch):
    _dns(monkeypatch)
    calls = []
    _patch_get(monkeypatch, [Resp(302, headers={"Location": "/next"}) for _ in range(10)], calls)
    with pytest.raises(DependencyUnavailableError) as ei:
        safe_fetch.fetch_public_text("http://ok.example/")
    assert len(calls) == safe_fetch.MAX_REDIRECTS + 1
    assert ei.value.message == safe_fetch.FETCH_FAILED


def test_byte_cap_stops_reading(monkeypatch):
    _dns(monkeypatch)
    big = b"<p>" + b"word " * 200_000 + b"</p>"
    resp = Resp(200, big)
    _patch_get(monkeypatch, [resp])
    res = safe_fetch.fetch_public_text("http://ok.example/", max_bytes=1000)
    assert resp.reads <= 1000 and resp.closed
    assert len(res.text) <= 1000


def test_fixed_error_text_no_url_or_exception_echoed(monkeypatch):
    _dns(monkeypatch)
    _patch_get(monkeypatch, [Resp(500)])
    with pytest.raises(DependencyUnavailableError) as ei:
        safe_fetch.fetch_public_text("http://ok.example/page?token=SECRET")
    msg = ei.value.message
    assert msg == safe_fetch.FETCH_FAILED
    assert "secret" not in msg.lower() and "ok.example" not in msg and "boom" not in msg
    assert ei.value.__cause__ is None


def test_dns_failure_is_fixed_text(monkeypatch):
    def fail(host, port, **kw):
        raise OSError("lookup of nowhere.example failed")
    monkeypatch.setattr(metadata_service.socket, "getaddrinfo", fail)
    with pytest.raises(DependencyUnavailableError) as ei:
        safe_fetch.fetch_public_text("http://nowhere.example/")
    assert "nowhere" not in ei.value.message


@pytest.mark.parametrize("html", [
    b"",
    b"<html><body><div id='root'></div><script>window.app=1</script>"
    b"<noscript>Enable JavaScript</noscript></body></html>",
])
def test_needs_manual_for_shell_page(monkeypatch, html):
    _dns(monkeypatch)
    _patch_get(monkeypatch, [Resp(200, html)])
    res = safe_fetch.fetch_public_text("http://ok.example/")
    assert res.needs_manual and res.text == ""
    assert "Paste the page text" in res.message


def test_result_text_is_redacted(monkeypatch):
    _dns(monkeypatch)
    key = "sk-ant-api03-" + "B" * 40
    body = ("<p>" + "filler text here. " * 30 + key + "</p>").encode()
    _patch_get(monkeypatch, [Resp(200, body)])
    assert key not in safe_fetch.fetch_public_text("http://ok.example/").text


class _Dripping:
    """Raw stream that hands back one byte per read, forever."""
    def __init__(self, clock):
        self.clock = clock
        self.reads = 0

    def read(self, n, decode_content=False):
        self.reads += 1
        self.clock["now"] += 5  # each drip takes 5 s of wall clock
        return b"x"


def _slow_clock(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(metadata_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(safe_fetch.time, "monotonic", lambda: clock["now"])
    return clock


def test_slow_drip_body_hits_the_wall_clock_deadline(monkeypatch):
    clock = _slow_clock(monkeypatch)
    _dns(monkeypatch)
    resp = Resp()
    resp.raw = _Dripping(clock)
    _patch_get(monkeypatch, [resp])
    with pytest.raises(DependencyUnavailableError):
        safe_fetch.fetch_public_text("http://ok.example/")
    assert resp.closed
    assert resp.raw.reads <= metadata_service.FETCH_DEADLINE // 5 + 2


def test_metadata_page_fetch_hits_the_wall_clock_deadline(monkeypatch):
    clock = _slow_clock(monkeypatch)
    _dns(monkeypatch)
    resp = Resp()
    resp.raw = _Dripping(clock)
    _patch_get(monkeypatch, [resp])
    with pytest.raises(DependencyUnavailableError):
        metadata_service._fetch_page_text("http://ok.example/")
    assert resp.closed
    assert resp.raw.reads <= metadata_service.FETCH_DEADLINE // 5 + 2
