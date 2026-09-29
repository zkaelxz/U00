"""B-28: the Playwright tiers must not reach private addresses (redirects,
scripts, subresources), and an unresolvable start URL drops the browser tiers."""
import http.server
import os
import socket
import threading

import pytest

import page_fetch
from services import url_guard
from sources import ladder
from sources.ladder import AccessTier, TierOutcome


def _fake_getaddrinfo(table):
    def fake(host, port, *a, **k):
        if host not in table:
            raise socket.gaierror("nope")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]
    return fake


class _Req:
    def __init__(self, url):
        self.url = url


class _Route:
    def __init__(self, url):
        self.request = _Req(url)
        self.result = None

    def continue_(self):
        self.result = "continue"

    def abort(self, code=None):
        self.result = "abort"


@pytest.fixture
def dns(monkeypatch):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _fake_getaddrinfo({
        "public.example": "93.184.216.34", "inner.example": "10.0.0.5",
        "meta.example": "169.254.169.254", "127.0.0.1": "127.0.0.1",
        "10.0.0.5": "10.0.0.5", "169.254.169.254": "169.254.169.254",
    }))


@pytest.mark.parametrize("url,expected", [
    ("https://public.example/a.js", "continue"),
    ("data:text/html,hi", "continue"),
    ("blob:https://public.example/uuid", "continue"),
    ("http://10.0.0.5/admin", "abort"),
    ("http://inner.example/", "abort"),
    ("http://169.254.169.254/latest/", "abort"),
    ("http://meta.example/", "abort"),
    ("http://127.0.0.1:8080/", "abort"),
    ("file:///etc/passwd", "abort"),
    ("ftp://public.example/", "abort"),
    ("http://unresolvable.example/", "abort"),
])
def test_guard_aborts_private_allows_public(dns, url, expected):
    route = _Route(url)
    page_fetch.make_request_guard()(route, route.request)
    assert route.result == expected


def test_guard_caches_per_host():
    calls = []
    guard = page_fetch.make_request_guard(resolver=lambda u: calls.append(u))
    for path in ("a", "b", "c"):
        r = _Route("https://public.example/" + path)
        guard(r, r.request)
    assert len(calls) == 1


class _FakeContext:
    def __init__(self):
        self.routes = []
        self.pages = []

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def new_page(self):
        return _FakePage()

    def close(self):
        pass

    def wait_for_event(self, *a, **k):
        pass


class _FakePage:
    def goto(self, *a, **k): pass
    def evaluate(self, *a, **k): return {}
    def wait_for_load_state(self, *a, **k): pass
    def wait_for_selector(self, *a, **k): pass
    def wait_for_timeout(self, *a, **k): pass
    def add_init_script(self, *a, **k): pass
    def on(self, *a, **k): pass
    def content(self): return "<html><body>x</body></html>"


class _FakeBrowser:
    def __init__(self, contexts):
        self.contexts = contexts

    def new_context(self, **kw):
        assert kw.get("service_workers") == "block"
        c = _FakeContext()
        self.contexts.append(c)
        return c

    def new_page(self, **kw):
        raise AssertionError("unguarded browser.new_page used")

    def close(self):
        pass


def _fake_playwright(contexts):
    class _Chromium:
        def launch(self, **kw):
            return _FakeBrowser(contexts)

    class _P:
        chromium = _Chromium()

        def __enter__(self): return self
        def __exit__(self, *a): return False

    return lambda: _P()


def test_every_launched_context_is_guarded(monkeypatch):
    contexts = []
    monkeypatch.setattr(page_fetch, "_require_playwright", lambda: _fake_playwright(contexts))
    page_fetch.fetch_rendered("https://public.example/", wait_ms=0)
    page_fetch.fetch_rendered_resolving_blobs("https://public.example/", wait_ms=0)
    with page_fetch.rendered_session("https://public.example/", wait_ms=0):
        pass
    with page_fetch.api_capture_session("https://public.example/", "x", wait_ms=0):
        pass
    assert len(contexts) == 4
    assert all(len(c.routes) == 1 and c.routes[0][0] == "**/*" for c in contexts)


def test_profile_contexts_are_guarded(tmp_path):
    made = []

    class _PW:
        def stop(self): pass

    def launcher(profile_dir, headless):
        c = _FakeContext()
        made.append(c)
        return _PW(), c

    page_fetch.fetch_with_profile("https://public.example/", str(tmp_path / "a"),
                                  wait_ms=0, launcher=launcher)
    page_fetch.open_login_window("https://public.example/", str(tmp_path / "b"),
                                 launcher=launcher)
    assert len(made) == 2 and all(len(c.routes) == 1 for c in made)


def test_unresolvable_url_skips_only_browser_tiers(monkeypatch):
    def unresolved(url):
        raise url_guard.URLResolveError(url_guard.RESOLVE_FAILED)
    monkeypatch.setattr(url_guard, "resolve_public", unresolved)
    ran = []

    def tier(name):
        def fn(url):
            ran.append(name)
            return TierOutcome(ok=False)
        return fn

    tiers = {AccessTier.STATIC_HTTP: tier("static"),
             AccessTier.RENDERED_BROWSER: tier("rendered"),
             AccessTier.AUTHENTICATED_BROWSER: tier("auth")}
    ladder.run_ladder("http://nowhere.invalid/", tiers, log=False)
    assert ran == ["static"]


_CHROMIUM = os.path.isdir(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "/opt/pw-browsers")


@pytest.mark.skipif(not _CHROMIUM, reason="no Playwright Chromium installed")
def test_real_browser_redirect_to_loopback_is_aborted(monkeypatch):
    pytest.importorskip("playwright.sync_api")
    hits = []

    class Private(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>SECRET-ADMIN</body></html>")

        def log_message(self, *a):
            pass

    priv = http.server.HTTPServer(("127.0.0.1", 0), Private)
    target = "http://127.0.0.1:%d/admin" % priv.server_port

    class Redirector(Private):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

    pub = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    for s in (priv, pub):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    pub_port = pub.server_port

    # The redirector's own origin stands in for the attacker's public site;
    # every other URL goes through the real check.
    real = url_guard.resolve_public

    def resolver(url):
        if url.startswith("http://127.0.0.1:%d/" % pub_port):
            return "127.0.0.1"
        return real(url)
    monkeypatch.setattr(url_guard, "resolve_public", resolver)
    try:
        try:
            html, _ = page_fetch.fetch_rendered("http://127.0.0.1:%d/" % pub_port,
                                                timeout=10, wait_ms=0)
        except Exception as exc:  # an aborted navigation may raise
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright's Chromium build is not installed")
            html = ""
        assert "SECRET-ADMIN" not in html
        assert hits == []
    finally:
        priv.shutdown()
        pub.shutdown()


@pytest.mark.skipif(not _CHROMIUM, reason="no Playwright Chromium installed")
def test_real_browser_public_page_still_renders_through_the_proxy(monkeypatch):
    pytest.importorskip("playwright.sync_api")

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>PUBLIC-OK</body></html>")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(url_guard, "resolve_public", lambda url: "127.0.0.1")
    try:
        try:
            html, _ = page_fetch.fetch_rendered("http://127.0.0.1:%d/" % srv.server_port,
                                                timeout=10, wait_ms=0)
        except Exception as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright's Chromium build is not installed")
            raise
        assert "PUBLIC-OK" in html
    finally:
        srv.shutdown()
