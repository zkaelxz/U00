"""B-25 residual: redirect targets in the pasted-URL import path are not
validated (no network).

`requests.adapters.HTTPAdapter.send` is replaced by a fake "internet", so
the real `requests` redirect machinery runs but nothing leaves the process.
`socket.getaddrinfo` maps the fake public hosts to a public IP, so a fix that
resolves and validates (or pins, like `metadata_service._pinned_get`) keeps
working here. A fetch is attributed to the Host header when one is set (a
pinned connection rewrites the URL to the IP), else to the URL's host.
"""
import io
import socket
from urllib.parse import urlsplit

import pytest

requests = pytest.importorskip("requests")

from sources.http import PacingPolicy, SourceClient, reset_pacing_state  # noqa: E402

PUBLIC_IP = "93.184.216.34"
PUBLIC_HOSTS = {"b23.tv", "www.bilibili.com", "example.org"}
PRIVATE_HOSTS = {"169.254.169.254", "127.0.0.1", "10.0.0.5"}
# Every URL the fake adapter was actually asked to connect to (a pinned
# request carries the validated IP here, an unpinned one the name).
SENT_URLS = []


def _no_pace_client(source: str) -> SourceClient:
    policy = PacingPolicy(min_delay=0, max_delay=0, max_retries=0,
                          session_break_min_requests=0, session_break_max_requests=0)
    return SourceClient(source, policy=policy, sleep=lambda s: None)


@pytest.fixture
def fake_net(monkeypatch, isolated_db):
    """routes: host -> (status, headers, body). Returns the list of hosts
    actually contacted."""
    reset_pacing_state()
    contacted = []
    routes = {}
    SENT_URLS.clear()

    def fake_send(self, request, **kwargs):
        host = request.headers.get("Host") or urlsplit(request.url).netloc
        host = host.split(":")[0].strip("[]").lower()
        contacted.append(host)
        SENT_URLS.append(request.url)
        status, headers, body = routes.get(host, (404, {}, b""))
        resp = requests.Response()
        resp.status_code = status
        resp.headers.update(headers)
        resp._content = body
        resp.raw = io.BytesIO(body)
        resp.url = request.url
        resp.request = request
        resp.encoding = "utf-8"
        return resp

    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, port, *a, **k):
        if host in PUBLIC_HOSTS:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port or 0))]
        if host in PRIVATE_HOSTS:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port or 0))]
        return real_getaddrinfo(host, port, *a, **k)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    for var in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    yield routes, contacted
    reset_pacing_state()


class TestB25RedirectTargets:
    def test_b23_short_link_redirect_to_metadata_ip_is_not_followed(self, fake_net):
        from sources.adapters.bilibili import BilibiliSource
        routes, contacted = fake_net
        routes["b23.tv"] = (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b"")
        routes["169.254.169.254"] = (200, {"Content-Type": "text/plain"}, b"iam-secret")

        a = BilibiliSource(client=_no_pace_client("bilibili"), allow_adult=False)
        try:
            a.normalize_url("https://b23.tv/abc123")
        except Exception:
            pass  # refusing is fine; contacting the private host is not
        assert "b23.tv" in contacted  # the short link itself was resolved
        assert not (set(contacted) & PRIVATE_HOSTS), contacted

    def test_resolved_short_link_to_non_bilibili_host_never_reaches_ytdlp(self, isolated_db):
        from sources.adapters.bilibili import BilibiliSource
        seen = []

        class _FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                seen.append(url)
                return {"id": "BV1xx411c7mD", "title": "t", "formats": []}

        a = BilibiliSource(allow_adult=False,
                           url_resolver=lambda u: "http://127.0.0.1:8000/video/BV1xx411c7mD",
                           ydl_factory=_FakeYDL, sleep=lambda s: None,
                           client=_no_pace_client("bilibili"))
        try:
            a.extract_info("https://b23.tv/abc123")
        except Exception:
            pass
        bad = [u for u in seen
               if not (urlsplit(u).hostname or "").endswith("bilibili.com")]
        assert bad == [], bad

    def test_front_door_preview_redirect_to_private_host_is_not_followed(self, fake_net):
        from sources import front_door
        routes, contacted = fake_net
        routes["example.org"] = (302, {"Location": "http://10.0.0.5/admin"}, b"")
        routes["10.0.0.5"] = (200, {"Content-Type": "text/html"},
                              b"<html><title>router admin</title><body>x</body></html>")

        def no_browser(url):
            raise RuntimeError("no browser in tests")

        try:
            front_door.preview("https://example.org/story", client=_no_pace_client("generic"),
                               rendered_fetch=no_browser)
        except Exception:
            pass
        assert "example.org" in contacted
        assert not (set(contacted) & PRIVATE_HOSTS), contacted


class TestB25TransportStillWorks:
    def test_public_cross_host_redirect_is_followed(self, fake_net):
        routes, contacted = fake_net
        routes["example.org"] = (302, {"Location": "https://www.bilibili.com/mirror"}, b"")
        routes["www.bilibili.com"] = (200, {"Content-Type": "text/plain"}, b"mirror-ok")
        resp = _no_pace_client("generic").request("GET", "https://example.org/start")
        assert resp.content == b"mirror-ok"
        assert resp.url == "https://www.bilibili.com/mirror"
        assert contacted == ["example.org", "www.bilibili.com"]

    def test_loopback_proxy_setting_does_not_refuse_a_public_target(self, fake_net):
        from sources import store
        routes, contacted = fake_net
        store.set_setting("http_proxy_url", "http://127.0.0.1:8080")
        routes["example.org"] = (200, {"Content-Type": "text/plain"}, b"via-proxy")
        resp = _no_pace_client("generic").request("GET", "https://example.org/p")
        assert resp.content == b"via-proxy"
        assert contacted == ["example.org"]

    def test_private_first_hop_is_refused(self, fake_net):
        from sources.http import UnsafeRedirect, _requests_transport
        _, contacted = fake_net
        with pytest.raises(UnsafeRedirect):
            _requests_transport("GET", "http://10.0.0.5/admin", {}, None, 5)
        assert contacted == []

    def test_redirect_hop_cap_is_enforced(self, fake_net):
        from sources.http import MAX_REDIRECTS, UnsafeRedirect, _requests_transport
        routes, contacted = fake_net
        routes["example.org"] = (302, {"Location": "/again"}, b"")
        with pytest.raises(UnsafeRedirect):
            _requests_transport("GET", "https://example.org/loop", {}, None, 5)
        assert len(contacted) == MAX_REDIRECTS + 1


class TestB25ReviewFixes:
    def test_request_is_pinned_to_the_validated_ip(self, fake_net):
        routes, _ = fake_net
        routes["example.org"] = (200, {"Content-Type": "text/plain"}, b"ok")
        _no_pace_client("generic").request("GET", "https://example.org/p")
        assert [urlsplit(u).hostname for u in SENT_URLS] == [PUBLIC_IP]

    def test_unicode_host_is_validated_in_the_form_requests_connects_to(self, fake_net, monkeypatch):
        """IDNA2003 (getaddrinfo) maps 'ß' to 'ss'; requests' UTS46 keeps it.
        The name validated must be the punycode requests will really use."""
        from sources.http import UnsafeRedirect, _requests_transport
        real = socket.getaddrinfo

        def gai(host, port, *a, **k):
            if host in ("strasse.example", "straße.example"):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]
            if host == "xn--strae-oqa.example":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]
            return real(host, port, *a, **k)
        monkeypatch.setattr(socket, "getaddrinfo", gai)
        with pytest.raises(UnsafeRedirect):
            _requests_transport("GET", "http://straße.example/", {}, None, 5)
        assert SENT_URLS == []

    def test_pin_host_mismatch_fails_closed(self, fake_net, monkeypatch):
        from sources import http
        monkeypatch.setattr(http, "_ascii_url", lambda u: u)   # skip normalisation
        real = socket.getaddrinfo
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, p))]
            if "stra" in h else real(h, p, *a, **k)))
        with pytest.raises(http.UnsafeRedirect):
            http._requests_transport("GET", "http://straße.example/", {}, None, 5)
        assert SENT_URLS == []

    def test_no_proxy_alone_keeps_pinning(self, fake_net, monkeypatch):
        routes, _ = fake_net
        monkeypatch.setenv("NO_PROXY", "internal.example")
        routes["example.org"] = (200, {}, b"ok")
        _no_pace_client("generic").request("GET", "https://example.org/p")
        assert urlsplit(SENT_URLS[0]).hostname == PUBLIC_IP

    def test_http_proxy_only_keeps_pinning_for_https(self, fake_net, monkeypatch):
        routes, _ = fake_net
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:3128")
        routes["example.org"] = (200, {}, b"ok")
        _no_pace_client("generic").request("GET", "https://example.org/p")
        assert urlsplit(SENT_URLS[0]).hostname == PUBLIC_IP

    def test_configured_proxy_sends_by_name(self, fake_net):
        from sources import store
        routes, _ = fake_net
        store.set_setting("http_proxy_url", "http://127.0.0.1:8080")
        routes["example.org"] = (200, {}, b"ok")
        _no_pace_client("generic").request("GET", "https://example.org/p")
        assert urlsplit(SENT_URLS[0]).hostname == "example.org"

    def test_cross_host_hop_drops_cookie_and_authorization(self, fake_net):
        from sources.http import _requests_transport
        routes, _ = fake_net
        seen = []
        routes["example.org"] = (302, {"Location": "https://www.bilibili.com/x"}, b"")
        routes["www.bilibili.com"] = (200, {}, b"ok")
        orig = requests.adapters.HTTPAdapter.send

        def spy(self, request, **kw):
            seen.append({k.lower() for k in request.headers})
            return orig(self, request, **kw)
        import unittest.mock as um
        with um.patch.object(requests.adapters.HTTPAdapter, "send", spy):
            _requests_transport("GET", "https://example.org/", {"Cookie": "a=b",
                                "Authorization": "Bearer t"}, None, 5)
        assert "cookie" in seen[0] and "authorization" in seen[0]
        assert "cookie" not in seen[1] and "authorization" not in seen[1]

    def test_refused_hop_never_falls_through_to_the_browser(self, fake_net, monkeypatch):
        import page_fetch
        from sources import front_door
        routes, contacted = fake_net
        rendered = []
        monkeypatch.setattr(page_fetch, "fetch_rendered",
                            lambda url, *a, **k: rendered.append(url) or "<html></html>")
        routes["example.org"] = (302, {"Location": "http://10.0.0.5/admin"}, b"")
        try:
            front_door.preview("https://example.org/story", client=_no_pace_client("generic"))
        except Exception:
            pass
        assert rendered == []
        assert not (set(contacted) & PRIVATE_HOSTS)

    def test_private_url_never_reaches_any_ladder_tier(self, fake_net):
        from sources import ladder
        from sources.models import AccessTier
        called = []
        tiers = {AccessTier.RENDERED_BROWSER: lambda u: called.append(u)}
        result = ladder.run_ladder("http://169.254.169.254/latest/", tiers, log=False)
        assert called == [] and not result.ok
