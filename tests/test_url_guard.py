"""
lib/url_guard.py on its own: every refusal of resolve_public (malformed URL,
scheme, credentials, unresolvable host, any non-public address in the
answer, tunnelled IPv6 forms), the address it pins, and the Referer and
redirect-header trimming. getaddrinfo is faked, so nothing is resolved.
"""
import socket

import pytest

from lib import url_guard as ug


def _answers(monkeypatch, *ips):
    seen = []

    def fake(host, port, type=0):
        seen.append((host, port))
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (ip, port)) for ip in ips]
    monkeypatch.setattr(ug.socket, "getaddrinfo", fake)
    return seen


@pytest.mark.parametrize("url", [
    None, 5, b"https://example.com/", "", "example.com", "ftp://example.com/",
    "file:///etc/passwd", "javascript:alert(1)", "https://", "https:///path",
    "https://user@example.com/", "https://user:pw@example.com/", "https://:pw@example.com/",
    "https://example.com:99999/", "https://example.com:port/", "http://[::1/",
])
def test_malformed_or_non_http_urls_are_refused_before_any_lookup(monkeypatch, url):
    seen = _answers(monkeypatch, "93.184.216.34")
    with pytest.raises(ug.UnsafeURLError) as exc:
        ug.resolve_public(url)
    assert str(exc.value) == ug.BAD_URL
    assert seen == []


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0",
    "100.64.0.1", "224.0.0.1", "255.255.255.255", "::1", "fe80::1", "fc00::1", "::",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a00:1", "2002:a00:1::1", "ff02::1", "ff0e::1", "239.1.1.1",
    "240.0.0.1", "198.18.0.1", "192.0.2.1", "2001:db8::1",
])
def test_a_non_public_address_is_refused(monkeypatch, ip):
    _answers(monkeypatch, ip)
    with pytest.raises(ug.UnsafeURLError) as exc:
        ug.resolve_public("https://example.com/")
    assert str(exc.value) == ug.NOT_PUBLIC
    assert ip not in str(exc.value)


def test_one_private_address_among_public_ones_refuses_the_whole_host(monkeypatch):
    _answers(monkeypatch, "93.184.216.34", "10.0.0.1")
    with pytest.raises(ug.UnsafeURLError):
        ug.resolve_public("https://example.com/")


def test_the_first_public_address_is_pinned_and_the_default_port_used(monkeypatch):
    seen = _answers(monkeypatch, "93.184.216.34", "2606:2800:220:1::1")
    assert ug.resolve_public("https://Example.com/a?b") == "93.184.216.34"
    assert ug.resolve_public("http://example.com/") == "93.184.216.34"
    assert ug.resolve_public("https://example.com:8443/") == "93.184.216.34"
    assert [port for _h, port in seen] == [443, 80, 8443]
    assert seen[0][0] == "example.com"


def test_a_scoped_ipv6_answer_is_judged_without_its_zone(monkeypatch):
    _answers(monkeypatch, "fe80::1%eth0")
    with pytest.raises(ug.UnsafeURLError):
        ug.resolve_public("https://example.com/")
    _answers(monkeypatch, "2606:2800:220:1::1%3")
    assert ug.resolve_public("https://example.com/") == "2606:2800:220:1::1"


def test_a_v4_mapped_public_address_is_allowed(monkeypatch):
    _answers(monkeypatch, "::ffff:93.184.216.34")
    assert ug.resolve_public("https://example.com/") == "::ffff:93.184.216.34"


def test_an_answer_that_is_not_an_address_is_refused(monkeypatch):
    _answers(monkeypatch, "not-an-ip")
    with pytest.raises(ug.UnsafeURLError) as exc:
        ug.resolve_public("https://example.com/")
    assert str(exc.value) == ug.NOT_PUBLIC


@pytest.mark.parametrize("err", [socket.gaierror(-2, "Name or service not known"),
                                 OSError("network down"), UnicodeError("label too long")])
def test_a_lookup_failure_is_a_resolve_error_with_no_host_in_it(monkeypatch, err):
    def fail(*_a, **_k):
        raise err
    monkeypatch.setattr(ug.socket, "getaddrinfo", fail)
    with pytest.raises(ug.URLResolveError) as exc:
        ug.resolve_public("https://secret-host.example/")
    assert str(exc.value) == ug.RESOLVE_FAILED
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_an_empty_answer_is_a_resolve_error(monkeypatch):
    _answers(monkeypatch)
    with pytest.raises(ug.URLResolveError):
        ug.resolve_public("https://example.com/")


def test_the_two_errors_keep_their_base_classes():
    # Callers catch ValueError for a refused URL and OSError for a network failure.
    assert issubclass(ug.UnsafeURLError, ValueError)
    assert issubclass(ug.URLResolveError, OSError)
    assert not issubclass(ug.URLResolveError, ValueError)


@pytest.mark.parametrize("url, origin", [
    ("https://example.com/chapter/1?token=abc#frag", "https://example.com/"),
    ("http://user:pw@example.com:8080/x", "http://example.com:8080/"),
    ("https://[2606:2800:220:1::1]:444/p", "https://[2606:2800:220:1::1]:444/"),
    ("https://example.com:notaport/x", "https://example.com/"),
    ("ftp://example.com/x", ""), ("/relative/path", ""), ("", ""), (None, ""),
])
def test_referer_origin(url, origin):
    assert ug.referer_origin(url) == origin


def test_cross_origin_headers_drop_credentials_and_trim_the_referer():
    out = ug.cross_origin_headers({
        "Authorization": "Bearer x", "COOKIE": "s=1", "Referer": "https://a.example/ch?t=1",
        "User-Agent": "UA", "Accept": "*/*"})
    assert out == {"Referer": "https://a.example/", "User-Agent": "UA", "Accept": "*/*"}


def test_cross_origin_headers_drop_a_referer_with_no_origin():
    assert ug.cross_origin_headers({"referer": "about:blank", "X": "1"}) == {"X": "1"}
