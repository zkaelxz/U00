"""
page_fetch.py -- fetching pages that may or may not be JavaScript-rendered.

Why this exists: a plain requests+BeautifulSoup fetch of a modern
single-page app returns the HTML *shell* -- navigation, filter controls,
empty containers -- and none of the actual content, because the data is
loaded by JavaScript after the page arrives. baihehub.com is exactly
this: fetching its novel listing statically returns the page furniture
and the literal text "共 0 条数据" (0 items).

The old behaviour was to hand that empty shell to the LLM extractor,
which would dutifully find nothing and report "couldn't extract
metadata" -- indistinguishable from a genuinely empty page. That's a
silent failure, and silent failures are worse than loud ones.

So this module does three things:
  1. Detects when a fetch returned a shell rather than content.
  2. Optionally renders the page properly with a headless browser.
  3. Falls back to an explicit "paste the text yourself" path that
     always works, instead of pretending.
"""

import os
import re
import threading
from contextlib import contextmanager

# Root containers common to SPA frameworks. Their presence alongside
# very little text is a strong signal the content hasn't rendered.
SPA_ROOT_MARKERS = [
    'id="app"', "id='app'", 'id="root"', "id='root'",
    'id="__next"', 'id="__nuxt"', 'data-reactroot', 'ng-app',
]

# Empty-state strings sites render before data loads.
EMPTY_STATE_MARKERS = [
    "共 0 条", "0 条数据", "暂无数据", "没有找到", "loading",
    "no results", "no data",
]


def looks_like_unrendered_shell(html: str, extracted_text: str) -> dict:
    """
    Heuristic check for "this fetch returned a shell, not content".

    Returns {"is_shell": bool, "confidence": float, "reasons": [str]}.
    Deliberately returns reasons rather than a bare boolean so the UI
    can tell the person *why* it thinks the fetch failed.
    """
    reasons = []
    score = 0.0

    text_len = len((extracted_text or "").strip())
    html_len = len(html or "")

    # A content page has a decent text-to-markup ratio. A shell doesn't.
    if html_len > 2000 and text_len < 600:
        reasons.append("very little text relative to page size")
        score += 0.4

    if any(m in (html or "") for m in SPA_ROOT_MARKERS):
        reasons.append("page uses a JavaScript app container")
        score += 0.3

    lowered = (extracted_text or "").lower()
    for marker in EMPTY_STATE_MARKERS:
        if marker in lowered or marker in (extracted_text or ""):
            reasons.append(f"page shows an empty/loading state ({marker!r})")
            score += 0.35
            break

    # Lots of <script> and little else.
    script_count = len(re.findall(r"<script", html or "", flags=re.I))
    if script_count > 8 and text_len < 1500:
        reasons.append(f"{script_count} scripts but little rendered text")
        score += 0.2

    return {
        "is_shell": score >= 0.5,
        "confidence": min(score, 1.0),
        "reasons": reasons,
    }


# B-28: a public page must not be able to redirect or script the browser onto
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
    from services import url_guard
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
        from services import url_guard
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
        browser = p.chromium.launch(headless=True, **proxy.launch_kwargs())
        try:
            yield browser, proxy
        finally:
            browser.close()
    finally:
        if proxy is not None:
            proxy.stop()


# id(persistent context) -> its _PinningProxy (stopped in _shut)
_PROXIES = {}


class ProxyBypassed(RuntimeError):
    """The browser loaded a page without going through the pinning proxy."""


_BYPASSED = ("The browser did not use Baihe's address-checking proxy, so the page "
             "was not loaded. (A browser policy may be overriding it.)")


def _goto(page, url: str, proxy, allow_unguarded: bool = False, **kwargs):
    """`page.goto`, then fail closed: with no proxy for this launch, or when
    an http(s) navigation returned a response but the pinning proxy saw no
    request at all (e.g. a managed browser policy overriding the proxy
    setting), the B-28 protection is not in force. `allow_unguarded` is
    only for an injected test launcher, which has no proxy."""
    if proxy is None and not allow_unguarded:
        raise ProxyBypassed(_BYPASSED)
    response = page.goto(url, **kwargs)
    if (proxy is not None and response is not None and proxy.proxied == 0
            and url.split(":", 1)[0].lower() in ("http", "https")):
        raise ProxyBypassed(_BYPASSED)
    return response


def _guard_context(context):
    """Install the B-28 request guard on a browser context."""
    context.route("**/*", make_request_guard())
    return context


def _guarded_page(browser):
    """A new page in a fresh guarded context (service workers blocked, as
    their requests would bypass routing)."""
    context = browser.new_context(user_agent=_UA, service_workers="block")
    _guard_context(context)
    return context.new_page()


def fetch_static(url: str, timeout: int = 20):
    """Plain fetch. Returns (html, text). Raises on network failure."""
    import requests
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (compatible; BaiheStudio/1.0)"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if l.strip())
    return html, text


def fetch_rendered(url: str, timeout: int = 30, wait_selector: str = None,
                    wait_ms: int = 2500):
    """
    Fetches with a real browser engine so JavaScript actually runs.

    Requires: pip install playwright && playwright install chromium
    (the second command downloads the browser itself -- easy to forget,
    so the error message below says so explicitly).

    Returns (html, text). Raises ImportError with install instructions
    if Playwright isn't set up.
    """
    with _rendered_page(url, timeout, wait_selector, wait_ms) as page:
        html = page.content()
    return html, _visible_lines(html)


# Resolves every `<img src="blob:...">` on the page into real bytes from
# inside that page's own JS context, before the browser (and with it, the
# blob's only storage) closes. Manhuaku's own real readPic() mechanism
# (Step 23j) writes decrypted page images into the DOM exactly this way --
# a blob: URL only exists in that one tab's memory and can never be
# independently re-fetched afterward. Chunked base64 encoding avoids
# blowing the call stack on a large image (a naive
# String.fromCharCode(...spread) over a multi-MB Uint8Array can).
_BLOB_RESOLVE_JS = """
async () => {
    const out = {};
    const imgs = Array.from(document.querySelectorAll('img[src^="blob:"]'));
    for (const img of imgs) {
        try {
            const resp = await fetch(img.src);
            const buf = new Uint8Array(await resp.arrayBuffer());
            let binary = '';
            const chunkSize = 8192;
            for (let i = 0; i < buf.length; i += chunkSize) {
                binary += String.fromCharCode.apply(null, buf.subarray(i, i + chunkSize));
            }
            out[img.src] = btoa(binary);
        } catch (e) {
            // left out; the Python side treats a missing key as
            // "couldn't capture", not an error
        }
    }
    return out;
}
"""


def fetch_rendered_resolving_blobs(url: str, timeout: int = 30, wait_selector: str = None,
                                   wait_ms: int = 2500):
    """Like fetch_rendered(), but additionally resolves any `blob:` object
    URLs found in `<img>` tags into real bytes before the browser closes.

    Returns (html, text, blob_bytes) -- blob_bytes maps each `blob:` URL
    string to the real bytes fetched from inside the page context. A blob
    whose fetch/decode failed is simply left out of the dict, not raised
    as an error here; the caller decides what a missing blob means.
    """
    import base64
    with _rendered_page(url, timeout, wait_selector, wait_ms) as page:
        html = page.content()
        raw = page.evaluate(_BLOB_RESOLVE_JS) or {}
    blob_bytes = {}
    for blob_url, b64 in raw.items():
        try:
            blob_bytes[blob_url] = base64.b64decode(b64)
        except (ValueError, TypeError):
            continue
    return html, _visible_lines(html), blob_bytes


# Keeps every Blob a page creates alive and its object URL resolvable.
# Injected before the site's own scripts run. Some viewers (mangaz.com's
# own, Step 23l) call URL.revokeObjectURL() inside the image's onload, so
# by the time anything else looks the blob is already gone -- the rendered
# bitmap is still on screen, but its bytes are unreachable. This only
# declines to throw away what the page itself already produced for
# display; it decodes nothing and defeats nothing.
_BLOB_KEEPALIVE_JS = """
window.__keptBlobs = {};
const __origCreateObjectURL = URL.createObjectURL.bind(URL);
URL.createObjectURL = function (obj) {
    const url = __origCreateObjectURL(obj);
    try { window.__keptBlobs[url] = obj; } catch (e) {}
    return url;
};
URL.revokeObjectURL = function () { /* kept resolvable on purpose */ };
"""

# Reads back the kept blobs, newest first is irrelevant -- keyed by the
# object URL the page itself handed to its own <img> tags, so a caller can
# tie each one to whatever element referenced it.
_KEPT_BLOBS_JS = """
async () => {
    const out = {};
    for (const [url, blob] of Object.entries(window.__keptBlobs || {})) {
        try {
            const buf = new Uint8Array(await blob.arrayBuffer());
            let binary = '';
            for (let i = 0; i < buf.length; i += 8192) {
                binary += String.fromCharCode.apply(null, buf.subarray(i, i + 8192));
            }
            out[url] = btoa(binary);
        } catch (e) {
            // left out; a missing key means "couldn't capture"
        }
    }
    return out;
}
"""


@contextmanager
def rendered_session(url: str, timeout: int = 30, wait_ms: int = 2500,
                     keep_blobs: bool = False):
    """An open, loaded page the caller drives itself, instead of the
    one-shot fetch_rendered() shape.

    For a site whose content only appears as its own viewer is navigated
    (mangaz.com's paginated reader, Step 23l): the caller steps through
    using that site's own public viewer API and reads what it produces,
    rather than this project reproducing the site's rendering itself.
    With `keep_blobs`, blobs the page creates stay resolvable for
    `kept_blob_bytes()` to read back.
    """
    sync_playwright = _require_playwright()
    with sync_playwright() as p:
        with _guarded_chromium(p) as (browser, proxy):
            page = _guarded_page(browser)
            if keep_blobs:
                page.add_init_script(_BLOB_KEEPALIVE_JS)
            _goto(page, url, proxy, timeout=timeout * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(wait_ms)
            yield page


def kept_blob_bytes(page) -> dict:
    """{object URL: real bytes} for every Blob a `keep_blobs` session's
    page has created so far. A blob that couldn't be read is left out."""
    import base64
    out = {}
    for blob_url, b64 in (page.evaluate(_KEPT_BLOBS_JS) or {}).items():
        try:
            out[blob_url] = base64.b64decode(b64)
        except (ValueError, TypeError):
            continue
    return out


def _url_matches(url: str, pattern) -> bool:
    """`pattern` is a plain substring or a compiled regex; either way,
    answers "does this response belong to the endpoint a caller is
    watching for"."""
    if hasattr(pattern, "search"):
        return bool(pattern.search(url))
    return pattern in url


def _capture_entry(url: str, status: int, content_type: str, body: bytes = None,
                   max_body_bytes: int = 5_000_000) -> dict:
    """One recorded response, with the body dropped (not raised past)
    when it's missing or larger than the cap -- so one huge, merely
    URL-matching download can't crowd out everything else a caller was
    watching for."""
    entry = {"url": url, "status": status, "content_type": content_type, "body": None}
    if body is not None and len(body) <= max_body_bytes:
        entry["body"] = body
    return entry


@contextmanager
def api_capture_session(url: str, url_pattern, timeout: int = 30, wait_ms: int = 3000,
                        max_body_bytes: int = 5_000_000):
    """Opens `url` in a real browser and records every network response
    whose URL matches `url_pattern`, as the page's own JavaScript makes
    them -- yielding `(page, captured)` so the caller can also drive the
    page further (click, scroll, call a viewer's own API) the same way
    `rendered_session()` already lets mangaz.py step through a reader.

    For a site that protects its own content API with something computed
    client-side -- a request signature built from a nonce, a timestamp,
    a device token and a salt; a rotating token; anything this project
    has no business reverse-engineering -- the site's own JavaScript
    already knows how to build a valid request. So let it: capture the
    real, already-signed request/response pairs the page makes on its
    own, instead of porting the signing algorithm to Python. The same
    "let the site's own execution path produce the result" principle
    mangaz.py and manhuaku.py already apply to descrambling and AES,
    extended here to an API a site protects with a computed signature
    rather than encrypted output. Nothing about the signature is ever
    inspected, guessed at, or reproduced -- only the response body the
    site's own request already earned.

    `url_pattern`: a substring, or a compiled regex, matched against
    each response's URL -- e.g. `"/api/chapter/"` or a compiled
    regex for a version-numbered content path.

    `captured` is a plain list that fills in live as matching responses
    arrive (read it after `page.wait_for_timeout()`, a click, or a
    `page.evaluate()` -- whatever the caller does inside the `with`).
    Each entry is `{"url", "status", "content_type", "body"}`; `body` is
    `None` when it couldn't be read (aborted, redirected away, or over
    `max_body_bytes`) rather than raising, since one bad response
    shouldn't cost the caller every other one that did work. Empty if
    the page never made a matching request at all -- that is itself a
    real finding (the content loads a different way than expected), not
    an error this function should paper over.
    """
    sync_playwright = _require_playwright()
    captured = []

    def on_response(response):
        if not _url_matches(response.url, url_pattern):
            return
        body = None
        try:
            content_length = response.headers.get("content-length")
            if not content_length or int(content_length) <= max_body_bytes:
                body = response.body()
        except Exception:
            body = None
        captured.append(_capture_entry(response.url, response.status,
                                       response.headers.get("content-type", ""),
                                       body, max_body_bytes))

    with sync_playwright() as p:
        with _guarded_chromium(p) as (browser, proxy):
            page = _guarded_page(browser)
            page.on("response", on_response)
            _goto(page, url, proxy, timeout=timeout * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(wait_ms)
            yield page, captured


@contextmanager
def _rendered_page(url: str, timeout: int, wait_selector: str, wait_ms: int):
    """A rendered, settled page, open for the caller to read from --
    closed automatically on exit. Shared by fetch_rendered() and
    fetch_rendered_resolving_blobs() so both wait the same way.

    Scrolls to the bottom once after the initial load: a real, confirmed
    need (manhuaku.net's chapter reader) for content some sites only
    populate on a scroll/resize event (jquery.lazyload and similar), not
    on the initial page load -- reproduced directly: the same chapter URL
    rendered with zero real reader images without this scroll, and real
    images consistently after it. Wrapped defensively, since a scroll can
    itself trigger a navigation on some sites (also observed directly: a
    responsive-redirect script reacting to the resulting resize event) --
    that isn't fatal, just settled with another wait."""
    sync_playwright = _require_playwright()
    with sync_playwright() as p:
        with _guarded_chromium(p) as (browser, proxy):
            page = _guarded_page(browser)
            _goto(page, url, proxy, timeout=timeout * 1000, wait_until="networkidle")
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_load_state("networkidle", timeout=timeout * 1000)
            except Exception:
                pass  # a scroll-triggered navigation or a slow settle isn't fatal
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except Exception:
                    pass  # selector guess was wrong; use whatever did render
            else:
                page.wait_for_timeout(wait_ms)
            yield page


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Rendering JavaScript pages needs Playwright:\n"
            "    pip install playwright\n"
            "    playwright install chromium\n"
            "The second command downloads the browser and is easy to miss."
        )
    return sync_playwright


def _visible_lines(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if l.strip())


# ---------------------------------------------------------------------------
# Persistent browser profiles (Step 23k)
# ---------------------------------------------------------------------------
#
# One Chromium profile directory per source, opened with Playwright's
# launch_persistent_context: whatever the person's own sign-in left in
# that profile (cookies, local storage) is there again on the next visit,
# so they aren't asked to log in on every import.
#
# What persists is the profile directory -- the browser process itself is
# started per call and closed after. Playwright's sync objects only work
# on the thread that created them (Streamlit runs each rerun on its own
# thread), a Chromium profile can only be open in one browser at a time,
# and the visible sign-in window and the headless reads need separate
# launches anyway. Login state survives all of that because it lives in
# the profile, which is exactly what a persistent context is for.
#
# The session data never leaves Chromium: nothing here reads cookies or
# storage state, and the only thing handed back is the page as rendered.

_PROFILE_LOCKS = {}
_PROFILE_LOCKS_GUARD = threading.Lock()
PROFILE_BUSY_WAIT = 120  # seconds a read waits for another use of the same profile


class ProfileBusy(RuntimeError):
    """The profile is already open (e.g. its sign-in window is still up)."""


def _profile_lock(profile_dir: str) -> threading.Lock:
    key = os.path.abspath(profile_dir)
    with _PROFILE_LOCKS_GUARD:
        return _PROFILE_LOCKS.setdefault(key, threading.Lock())


def _launch_persistent(profile_dir: str, headless: bool):
    """(playwright, context) for one persistent-profile launch. The
    browser's own user agent is kept -- the same browser the person signed
    in with, not a disguised one."""
    sync_playwright = _require_playwright()
    pw = sync_playwright().start()
    proxy = None
    try:
        proxy = _PinningProxy()
        context = pw.chromium.launch_persistent_context(profile_dir, headless=headless,
                                                        service_workers="block",
                                                        **proxy.launch_kwargs())
    except Exception:
        if proxy is not None:
            proxy.stop()
        pw.stop()
        raise
    _PROXIES[id(context)] = proxy
    return pw, context


def _shut(pw, context):
    for fn in (getattr(context, "close", None), getattr(pw, "stop", None)):
        try:
            if fn is not None:
                fn()
        except Exception:
            pass
    proxy = _PROXIES.pop(id(context), None)
    if proxy is not None:
        proxy.stop()


def fetch_with_profile(url: str, profile_dir: str, timeout: int = 30, wait_selector: str = None,
                       wait_ms: int = 2500, launcher=None):
    """Like fetch_rendered, but inside the persistent profile at
    `profile_dir`, so a site the person already signed in to sees that
    same signed-in browser. Returns (html, text). `launcher(profile_dir,
    headless)` -> (playwright, context) is injectable for tests."""
    lock = _profile_lock(profile_dir)
    if not lock.acquire(timeout=PROFILE_BUSY_WAIT):
        raise ProfileBusy("This site's browser profile is still in use (is its sign-in "
                          "window still open?). Finish there and close it first.")
    try:
        os.makedirs(profile_dir, exist_ok=True)
        pw, context = (launcher or _launch_persistent)(profile_dir, True)
        try:
            _guard_context(context)
            page = context.new_page()
            _goto(page, url, _PROXIES.get(id(context)), allow_unguarded=launcher is not None,
                  timeout=timeout * 1000, wait_until="networkidle")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except Exception:
                    pass
            else:
                page.wait_for_timeout(wait_ms)
            html = page.content()
        finally:
            _shut(pw, context)
    finally:
        lock.release()
    return html, _visible_lines(html)


def open_login_window(url: str, profile_dir: str, launcher=None):
    """Opens a visible browser window on the persistent profile at `url`
    and waits -- with no timeout -- until the person closes it. They sign
    in (and pass any CAPTCHA/MFA the site asks for) themselves, the normal
    way; nothing here types, clicks, solves or reads anything."""
    lock = _profile_lock(profile_dir)
    if not lock.acquire(blocking=False):
        raise ProfileBusy("This site's browser profile is already open -- finish in that "
                          "window (or wait for the import using it) first.")
    try:
        os.makedirs(profile_dir, exist_ok=True)
        pw, context = (launcher or _launch_persistent)(profile_dir, False)
        try:
            _guard_context(context)
            page = context.pages[0] if context.pages else context.new_page()
            proxy = _PROXIES.get(id(context))
            try:
                _goto(page, url, proxy, allow_unguarded=launcher is not None,
                      wait_until="domcontentloaded")
            except ProxyBypassed:
                raise
            except Exception:
                # The window stays open for the person to navigate -- but only
                # if the proxy demonstrably carries this browser's traffic.
                if proxy is not None and proxy.proxied == 0:
                    raise ProxyBypassed(_BYPASSED) from None  # the window is still open; the person can navigate there themselves
            context.wait_for_event("close", timeout=0)   # 0 = wait for the person, however long
        finally:
            _shut(pw, context)
    finally:
        lock.release()


def smart_fetch(url: str, allow_render: bool = True, timeout: int = 20):
    """
    Fetches a page and tells you honestly what you got.

    Tries a static fetch first (fast, no dependencies). If that looks
    like an unrendered shell and allow_render is on, retries with a
    headless browser. Never silently returns an empty shell as though
    it were real content.

    Returns:
      {"text": str, "method": "static"|"rendered"|"failed",
       "shell_check": {...}, "needs_manual": bool, "message": str}
    """
    result = {"text": "", "method": "failed", "shell_check": {},
              "needs_manual": False, "message": ""}

    try:
        html, text = fetch_static(url, timeout=timeout)
    except Exception as e:
        result["needs_manual"] = True
        result["message"] = f"Couldn't reach that page: {e}"
        return result

    check = looks_like_unrendered_shell(html, text)
    result["shell_check"] = check

    if not check["is_shell"]:
        result["text"] = text
        result["method"] = "static"
        result["message"] = "Fetched successfully."
        return result

    # It's a shell. Try rendering it properly.
    if allow_render:
        try:
            _html_r, text_r = fetch_rendered(url, timeout=max(timeout, 30))
            recheck = looks_like_unrendered_shell(_html_r, text_r)
            if not recheck["is_shell"]:
                result["text"] = text_r
                result["method"] = "rendered"
                result["message"] = "This page needed JavaScript; rendered it with a browser."
                result["shell_check"] = recheck
                return result
            result["text"] = text_r
            result["method"] = "rendered"
            result["needs_manual"] = True
            result["message"] = (
                "Rendered the page, but it still looks empty -- the content may be "
                "behind a login, or loaded only after interaction. "
                "Try the manual paste option.")
            result["shell_check"] = recheck
            return result
        except ImportError as e:
            result["needs_manual"] = True
            result["message"] = (
                "This page is built with JavaScript, so a plain fetch only returns an "
                f"empty shell.\n\n{e}\n\nOr use the manual paste option below.")
            return result
        except Exception as e:
            result["needs_manual"] = True
            result["message"] = f"Browser rendering failed: {e}. Try the manual paste option."
            return result

    result["needs_manual"] = True
    result["message"] = (
        "This page is built with JavaScript -- a plain fetch returns only the page "
        "furniture, not the listings. Enable browser rendering or paste the text manually.")
    return result


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

# Sites known to send X-Frame-Options / frame-ancestors headers that stop
# them being embedded. Not exhaustive -- most large sites do this.
KNOWN_FRAME_BLOCKERS = [
    "jjwxc.net", "missevan.com", "bilibili.com", "kuaikanmanhua.com",
    "naver.com", "kakao.com", "lezhin.com", "ridibooks.com",
    "bookwalker.jp", "dlsite.com", "fantia.jp",
]


def can_probably_embed(url: str) -> dict:
    """
    Best-effort guess at whether a URL can be shown in an iframe.

    Most substantial sites block framing for clickjacking protection, so
    an embedded browser panel will usually render blank. This lets the UI
    warn up front rather than showing an empty box and leaving you to
    wonder what broke.
    """
    lowered = (url or "").lower()
    for blocker in KNOWN_FRAME_BLOCKERS:
        if blocker in lowered:
            return {"embeddable": False, "reason": f"{blocker} blocks iframe embedding",
                    "certain": True}
    return {"embeddable": True,
            "reason": "Not on the known-blocked list, but many sites block framing -- "
                      "if the panel below is blank, that's why.",
            "certain": False}
