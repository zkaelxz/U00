"""
page_fetch_guard.py -- keeps a page-fetch browser off private addresses.

A local pinning proxy validates every target host, a route handler aborts
non-public first hops, and `_goto` fails closed when the proxy was bypassed.
"""

import threading
from contextlib import contextmanager

from page_fetch_browser import _launch_chromium


# A public page must not be able to redirect or script the browser onto
# a private address. Two layers:
#  1. Every Chromium this module launches sends ALL its traffic (navigations,
#     every redirect hop, subresources, fetch/XHR, WebSockets) through a
#     local proxy, `_PinningProxy`, which checks each target host with
#     `url_guard.resolve_public` and connects to the validated IP itself --
#     so Chromium never resolves a name (no DNS-rebinding window) and never
#     reaches a private address. Needed because Playwright's `route()` does
#     not see redirect hops (verified: a 302 to 127.0.0.1 is followed even
#     when the route handler fulfils the 302 itself).
#  2. Every context also installs `make_request_guard()` via
#     `context.route("**/*", ...)`: an early, cheap abort for non-http(s)
#     schemes and non-public first-hop requests.
_UA = "Mozilla/5.0 (compatible; BaiheStudio/1.0)"


def make_request_guard(resolver=None):
    """A Playwright route handler that aborts any request whose URL is not
    `data:`/`blob:` or an http(s) URL whose host resolves only to public
    addresses. Resolutions are cached per host for this handler's life."""
    from urllib.parse import urlsplit
    from lib import url_guard
    resolve = resolver or (lambda u: url_guard.resolve_public(u))
    cache = {}

    def allowed(url: str) -> bool:
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme in ("data", "blob"):
            return True
        if scheme not in ("http", "https"):
            return False
        try:
            parts = urlsplit(url)
            key = (scheme, (parts.hostname or "").lower(), parts.port)
        except ValueError:
            return False
        if key not in cache:
            try:
                resolve(url)
                cache[key] = True
            except Exception:
                cache[key] = False
        return cache[key]

    def handler(route, request=None):
        req = request if request is not None else route.request
        if allowed(req.url):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    handler.allowed = allowed
    return handler


_PROXY_IO_TIMEOUT = 30          # seconds; every proxied socket op is bounded
_PROXY_REFUSED = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_PROXY_AUTH_REQUIRED = (b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                        b'Proxy-Authenticate: Basic realm="baihe"\r\n'
                        b"Content-Length: 0\r\nConnection: close\r\n\r\n")


class _PinningProxy:
    """A tiny local HTTP/CONNECT proxy for one browser launch. Every target
    is validated with `url_guard.resolve_public` and the upstream socket is
    connected to the validated IP (pinned). One request per plain-http
    connection (`Connection: close` upstream), so a reused proxy connection
    can never carry a request for a different host."""

    def __init__(self):
        import base64
        import secrets
        import socketserver
        outer = self
        # A per-launch secret, so no other local process can use this proxy
        # as a server-side fetcher.
        self.username = secrets.token_urlsafe(16)
        self.password = secrets.token_urlsafe(24)
        self._expected_auth = "Basic " + base64.b64encode(
            ("%s:%s" % (self.username, self.password)).encode()).decode()
        self.proxied = 0          # authenticated requests seen (fail-closed check in _goto)
        self._count_lock = threading.Lock()

        class _Handler(socketserver.BaseRequestHandler):
            def handle(self):
                outer._handle(self.request)

        class _Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        self._server = _Server(("127.0.0.1", 0), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass

    def launch_kwargs(self):
        """Keyword arguments for `chromium.launch` / `launch_persistent_context`.
        `<-loopback>` removes Chromium's implicit loopback/link-local bypass,
        so even 127.0.0.1 and 169.254.x go through (and are refused by) the
        proxy. WebRTC is kept off non-proxied UDP and QUIC is disabled, so
        neither goes around the proxy."""
        return {
            "proxy": {"server": "http://127.0.0.1:%d" % self.port, "bypass": "<-loopback>",
                      "username": self.username, "password": self.password},
            "args": ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                     "--disable-quic"],
        }

    def _authorized(self, lines) -> bool:
        import hmac
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "proxy-authorization":
                return hmac.compare_digest(value.strip().encode("latin-1"),
                                           self._expected_auth.encode("latin-1"))
        return False

    @staticmethod
    def _parse_connect_target(target: str):
        """(host, port) from a CONNECT target (`host:port` or `[v6]:port`);
        ValueError on anything malformed or a port outside 1..65535."""
        if target.startswith("["):
            end = target.find("]")
            if end < 0 or target[end + 1:end + 2] != ":":
                raise ValueError("bad CONNECT target")
            host, port_s = target[1:end], target[end + 2:]
        else:
            host, sep, port_s = target.rpartition(":")
            if not sep or ":" in host:
                raise ValueError("bad CONNECT target")
        if not host or not port_s.isdigit():
            raise ValueError("bad CONNECT target")
        port = int(port_s)
        if not 1 <= port <= 65535:
            raise ValueError("bad CONNECT port")
        return host, port

    @staticmethod
    def _pinned(url):
        from lib import url_guard
        return url_guard.resolve_public(url)

    def _handle(self, client):
        import socket
        client.settimeout(_PROXY_IO_TIMEOUT)
        upstream = None
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(65536)
                if not chunk:
                    return
                head += chunk
                if len(head) > 65536:
                    return
            header_blob, rest = head.split(b"\r\n\r\n", 1)
            lines = header_blob.decode("latin-1").split("\r\n")
            method, target, version = lines[0].split(" ", 2)
            if not self._authorized(lines):
                client.sendall(_PROXY_AUTH_REQUIRED)
                return
            with self._count_lock:
                self.proxied += 1
            if method.upper() == "CONNECT":
                host, port = self._parse_connect_target(target)
                ip = self._pinned("https://%s:%d/" % (
                    "[%s]" % host if ":" in host else host, port))
                upstream = socket.create_connection((ip, port), timeout=_PROXY_IO_TIMEOUT)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
            else:
                from urllib.parse import urlsplit
                parts = urlsplit(target)
                if parts.scheme != "http":
                    raise ValueError("only absolute http:// targets are proxied")
                ip = self._pinned(target)
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                kept = [l for l in lines[1:] if l.split(":", 1)[0].strip().lower()
                        not in ("connection", "proxy-connection", "proxy-authorization",
                                "keep-alive")]
                is_upgrade = any(l.split(":", 1)[0].strip().lower() == "upgrade" for l in kept)
                kept.append("Connection: upgrade" if is_upgrade else "Connection: close")
                request = ("%s %s %s\r\n" % (method, path, version)
                           + "".join(l + "\r\n" for l in kept) + "\r\n").encode("latin-1")
                upstream = socket.create_connection((ip, parts.port or 80),
                                                    timeout=_PROXY_IO_TIMEOUT)
                upstream.sendall(request + rest)
            self._pipe(client, upstream)
        except Exception:
            try:
                if upstream is None:
                    client.sendall(_PROXY_REFUSED)
            except Exception:
                pass
        finally:
            for sock in (upstream, client):
                try:
                    if sock is not None:
                        sock.close()
                except Exception:
                    pass

    @staticmethod
    def _pipe(a, b):
        import select
        socks = [a, b]
        while True:
            ready, _, _ = select.select(socks, [], [], _PROXY_IO_TIMEOUT)
            if not ready:
                return
            for src in ready:
                data = src.recv(65536)
                if not data:
                    return
                (b if src is a else a).sendall(data)


@contextmanager
def _guarded_chromium(p):
    """(browser, proxy): a headless Chromium launched behind a fresh
    `_PinningProxy`. Pass the proxy to `_goto`."""
    proxy = None
    try:
        proxy = _PinningProxy()
        browser = _launch_chromium(p.chromium.launch, headless=True,
                                   **proxy.launch_kwargs())
        try:
            yield browser, proxy
        finally:
            browser.close()
    finally:
        if proxy is not None:
            proxy.stop()


class ProxyBypassed(RuntimeError):
    """The browser loaded a page without going through the pinning proxy."""


_BYPASSED = ("The browser did not use Baihe's address-checking proxy, so the page "
             "was not loaded. (A browser policy may be overriding it.)")


def _goto(page, url: str, proxy, allow_unguarded: bool = False, **kwargs):
    """`page.goto`, then fail closed: with no proxy for this launch, or when
    an http(s) navigation returned a response but the pinning proxy saw no
    request at all (e.g. a managed browser policy overriding the proxy
    setting), the private-address protection is not in force. `allow_unguarded` is
    only for an injected test launcher, which has no proxy."""
    if proxy is None and not allow_unguarded:
        raise ProxyBypassed(_BYPASSED)
    response = page.goto(url, **kwargs)
    if (proxy is not None and response is not None and proxy.proxied == 0
            and url.split(":", 1)[0].lower() in ("http", "https")):
        raise ProxyBypassed(_BYPASSED)
    return response


def _guard_context(context):
    """Install the private-address request guard on a browser context."""
    context.route("**/*", make_request_guard())
    return context


def _guarded_page(browser):
    """A new page in a fresh guarded context (service workers blocked, as
    their requests would bypass routing)."""
    context = browser.new_context(user_agent=_UA, service_workers="block")
    _guard_context(context)
    return context.new_page()
