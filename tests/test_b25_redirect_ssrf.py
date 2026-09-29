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

    def fake_send(self, request, **kwargs):
        host = request.headers.get("Host") or urlsplit(request.url).netloc
        host = host.split(":")[0].strip("[]").lower()
        contacted.append(host)
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
    yield routes, contacted
    reset_pacing_state()


class TestB25RedirectTargets:
    @pytest.mark.xfail(strict=True, reason="B-25: b23.tv HEAD (sources.http._requests_transport, "
                       "allow_redirects=True) follows a redirect to a link-local/private host")
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

    @pytest.mark.xfail(strict=True, reason="B-25: BilibiliSource.normalize_url returns whatever the "
                       "b23.tv resolver landed on and hands it to yt-dlp unchecked")
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

    @pytest.mark.xfail(strict=True, reason="B-25: front_door.preview's static fetch follows a "
                       "redirect from a public page to a private address")
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
