"""B-28: the Playwright tiers must not reach private addresses (redirects,
scripts, subresources), and an unresolvable start URL drops the browser tiers."""
import http.server
import os
import socket
import threading

import pytest

import page_fetch
from lib import url_guard
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


def _browser_dirs():
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        yield env
    yield "/opt/pw-browsers"
    # Playwright's default install locations (Windows, macOS, Linux).
    if os.environ.get("LOCALAPPDATA"):
        yield os.path.join(os.environ["LOCALAPPDATA"], "ms-playwright")
    home = os.path.expanduser("~")
    yield os.path.join(home, "Library", "Caches", "ms-playwright")
    yield os.path.join(home, ".cache", "ms-playwright")


_CHROMIUM = any(os.path.isdir(d) and any(n.startswith("chromium") for n in os.listdir(d))
                for d in _browser_dirs())


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
    made = []
    real_proxy = page_fetch._PinningProxy

    def recording_proxy():
        px = real_proxy()
        made.append(px)
        return px
    monkeypatch.setattr(page_fetch, "_PinningProxy", recording_proxy)
    try:
        try:
            html, _ = page_fetch.fetch_rendered("http://127.0.0.1:%d/" % srv.server_port,
                                                timeout=10, wait_ms=0)
        except Exception as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright's Chromium build is not installed")
            raise
        assert "PUBLIC-OK" in html
        assert len(made) == 1 and made[0].proxied > 0   # the page really came via the proxy
    finally:
        srv.shutdown()


# ---- the pinning proxy at socket level ----------------------------------

import base64  # noqa: E402


def _proxy_request(proxy, line, auth=True):
    sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    hdrs = "Host: x\r\n"
    if auth:
        token = base64.b64encode(("%s:%s" % (proxy.username, proxy.password)).encode()).decode()
        hdrs += "Proxy-Authorization: Basic %s\r\n" % token
    sock.sendall(("%s\r\n%s\r\n" % (line, hdrs)).encode())
    return sock


def _read_head(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.decode("latin-1")


@pytest.fixture
def proxy():
    px = page_fetch._PinningProxy()
    yield px
    px.stop()


def test_proxy_requires_the_per_launch_secret(proxy):
    for line in ("CONNECT public.example:443 HTTP/1.1", "GET http://public.example/ HTTP/1.1"):
        sock = _proxy_request(proxy, line, auth=False)
        head = _read_head(sock)
        sock.close()
        assert head.startswith("HTTP/1.1 407")
        assert 'Proxy-Authenticate: Basic realm="baihe"' in head
    assert proxy.proxied == 0


def test_proxy_rejects_a_wrong_secret(proxy):
    sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    sock.sendall(b"CONNECT public.example:443 HTTP/1.1\r\n"
                 b"Proxy-Authorization: Basic d3Jvbmc6d3Jvbmc=\r\n\r\n")
    assert _read_head(sock).startswith("HTTP/1.1 407")
    sock.close()


@pytest.mark.parametrize("line", [
    "CONNECT 10.0.0.5:443 HTTP/1.1",
    "CONNECT 169.254.169.254:80 HTTP/1.1",
    "CONNECT [::1]:443 HTTP/1.1",
    "GET http://127.0.0.1:1/ HTTP/1.1",
    "GET http://10.0.0.5/admin HTTP/1.1",
])
def test_proxy_refuses_private_targets(dns, proxy, line):
    sock = _proxy_request(proxy, line)
    assert _read_head(sock).startswith("HTTP/1.1 403")
    sock.close()


def test_connect_tunnels_to_exactly_the_validated_ip(monkeypatch, proxy):
    echo = socket.socket()
    echo.bind(("127.0.0.1", 0))
    echo.listen(1)
    port = echo.getsockname()[1]

    def serve():
        conn, _ = echo.accept()
        conn.sendall(conn.recv(100).upper())
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    seen = []

    def resolver(url):
        seen.append(url)
        return "127.0.0.1"          # the name itself resolves nowhere
    monkeypatch.setattr(url_guard, "resolve_public", resolver)
    sock = _proxy_request(proxy, "CONNECT pinned.invalid:%d HTTP/1.1" % port)
    assert _read_head(sock).startswith("HTTP/1.1 200")
    sock.sendall(b"hello")
    assert sock.recv(100) == b"HELLO"
    sock.close()
    echo.close()
    assert seen == ["https://pinned.invalid:%d/" % port]
    assert proxy.proxied == 1


@pytest.mark.parametrize("target,expected", [
    ("example.com:443", ("example.com", 443)),
    ("[2001:db8::1]:8443", ("2001:db8::1", 8443)),
    ("1.2.3.4:1", ("1.2.3.4", 1)),
    ("x:65535", ("x", 65535)),
])
def test_connect_target_parsing(target, expected):
    assert page_fetch._PinningProxy._parse_connect_target(target) == expected


@pytest.mark.parametrize("target", [
    "example.com", "example.com:", "example.com:0", "example.com:65536", "example.com:-1",
    "example.com:abc", "[2001:db8::1]", "[2001:db8::1]443", "2001:db8::1:443", ":443",
])
def test_connect_target_parsing_rejects_bad_targets(target):
    with pytest.raises(ValueError):
        page_fetch._PinningProxy._parse_connect_target(target)


def test_launch_kwargs_carry_secret_bypass_and_webrtc_flags(proxy):
    kw = proxy.launch_kwargs()
    assert kw["proxy"]["bypass"] == "<-loopback>"
    assert kw["proxy"]["username"] == proxy.username and kw["proxy"]["password"]
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in kw["args"]
    assert "--disable-quic" in kw["args"]


def test_goto_fails_closed_when_the_proxy_saw_nothing(proxy):
    class Ctx:
        browser = None

    class Page:
        context = Ctx()

        def goto(self, url, **kw):
            return object()     # a "response" that never touched the proxy

    page = Page()
    with pytest.raises(page_fetch.ProxyBypassed):
        page_fetch._goto(page, "https://public.example/", proxy)
    proxy.proxied = 1
    page_fetch._goto(page, "https://public.example/", proxy)


def test_goto_fails_closed_without_a_proxy():
    class Page:
        def goto(self, url, **kw):
            raise AssertionError("must not navigate without a proxy")

    with pytest.raises(page_fetch.ProxyBypassed):
        page_fetch._goto(Page(), "https://public.example/", None)


def test_login_window_fails_closed_when_the_first_load_saw_no_proxy_traffic(tmp_path, proxy):
    class Page:
        def goto(self, url, **kw):
            raise RuntimeError("net::ERR_SOMETHING")

    class Ctx:
        pages = []
        waited = False

        def route(self, *a):
            pass

        def new_page(self):
            return Page()

        def wait_for_event(self, *a, **k):
            Ctx.waited = True

        def close(self):
            pass

    class PW:
        def stop(self):
            pass

    ctx = Ctx()

    def launcher(profile_dir, headless):
        page_fetch._PROXIES[id(ctx)] = proxy    # as _launch_persistent does
        return PW(), ctx

    with pytest.raises(page_fetch.ProxyBypassed):
        page_fetch.open_login_window("https://public.example/", str(tmp_path / "p"),
                                     launcher=launcher)
    assert not Ctx.waited
    assert id(ctx) not in page_fetch._PROXIES


@pytest.mark.skipif(not _CHROMIUM, reason="no Playwright Chromium installed")
def test_real_browser_redirect_to_link_local_is_refused_by_the_proxy(monkeypatch):
    pytest.importorskip("playwright.sync_api")

    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()

        def log_message(self, *a):
            pass

    pub = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    threading.Thread(target=pub.serve_forever, daemon=True).start()
    real = url_guard.resolve_public
    checked = []

    def resolver(url):
        checked.append(url)
        if url.startswith("http://127.0.0.1:%d/" % pub.server_port):
            return "127.0.0.1"
        return real(url)
    monkeypatch.setattr(url_guard, "resolve_public", resolver)
    try:
        try:
            html, _ = page_fetch.fetch_rendered("http://127.0.0.1:%d/" % pub.server_port,
                                                timeout=10, wait_ms=0)
        except Exception as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright's Chromium build is not installed")
            html = ""
        # Playwright's route() never sees the redirect hop, so the only thing
        # that can have checked the 169.254 URL is the proxy: <-loopback>
        # removed Chromium's implicit link-local bypass.
        assert any(u.startswith("http://169.254.169.254/") for u in checked)
        assert "ami-id" not in html
    finally:
        pub.shutdown()


@pytest.mark.skipif(not _CHROMIUM, reason="no Playwright Chromium installed")
def test_real_browser_https_goes_through_the_authenticated_connect_tunnel(monkeypatch, tmp_path):
    pytest.importorskip("playwright.sync_api")
    import ssl
    import subprocess
    if subprocess.run(["openssl", "version"], capture_output=True).returncode != 0:
        pytest.skip("openssl not available to make a test certificate")
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True, timeout=60)

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>TLS-OK</body></html>")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Page)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(cert), str(key))
    srv.socket = tls.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    checked = []

    def resolver(url):
        checked.append(url)
        return "127.0.0.1"
    monkeypatch.setattr(url_guard, "resolve_public", resolver)
    url = "https://127.0.0.1:%d/" % srv.server_port
    try:
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                with page_fetch._guarded_chromium(p) as (browser, proxy):
                    context = browser.new_context(ignore_https_errors=True,
                                                  service_workers="block")
                    page_fetch._guard_context(context)
                    page = context.new_page()
                    page_fetch._goto(page, url, proxy, timeout=10000)
                    html = page.content()
                    tunnelled = proxy.proxied
        except Exception as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright's Chromium build is not installed")
            raise
        assert "TLS-OK" in html
        assert tunnelled > 0
        # the CONNECT path validated the target by name as https://host:port/
        assert "https://127.0.0.1:%d/" % srv.server_port in checked
    finally:
        srv.shutdown()
