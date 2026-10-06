"""
services/egress_proxy.py -- a loopback HTTP proxy that only reaches public
addresses, for tools that open URLs on their own (ffmpeg, yt-dlp).

Checking a URL once in Python and then handing it to ffmpeg or yt-dlp is
not enough: they follow redirects, open every HLS/DASH variant, segment
and key URI a playlist names, and resolve DNS again at connect time. Run
them with this proxy instead (ffmpeg: the `http_proxy` environment
variable; yt-dlp: the `proxy` option) and every connection they make goes
through `_open_upstream`: the target host is resolved and checked by
url_guard.resolve_public (every address global) and the socket connects
to that validated address, so a redirect, a playlist entry or a DNS
answer that changes after the check cannot reach loopback, the LAN or a
metadata address.

Two request forms are served: `CONNECT host:port` (https, tunnelled
unchanged, so TLS and SNI are the client's own) and an absolute-form
`http://` request, forwarded once with `Connection: close` (so one
client connection never carries a second request to another host).
Anything else is refused. The listener binds 127.0.0.1 on a random port
and answers only requests carrying its per-proxy random secret (Basic
`Proxy-Authorization`; clients take it from the userinfo of `proxy.url`),
so another local process can't borrow it while a session runs. Ports a
browser refuses to fetch from (the Fetch standard's "bad ports": SMTP,
SSH, IRC and the like) are refused on every host; no media server uses
them.

Known gap (ffmpeg 6.1): ffmpeg's HLS demuxer connects to host:port itself,
bypassing this proxy, for a `tcp://host:port/` variant URI (sends nothing,
reads the reply as a playlist) and for an `httpproxy://host:port/dest`
variant, segment or key URI (accepted because its name starts with
"http"). The latter sends one fixed `CONNECT dest HTTP/1.1` request (dest
from a playlist line, which can't hold CR or LF), and only a `200` reply
lets bytes through, read as media. Neither protocol can leave the
whitelist: ffmpeg tunnels https to a proxy only through httpproxy, over
tcp. The DASH and IMF demuxers, whose XML entries can carry a decoded
CR LF or a tcp:// path, are kept out by live_service's format whitelist.

Standard library only.
"""
import base64
import hmac
import secrets
import select
import socket
import threading
import time
from urllib.parse import urlsplit

from services import url_guard

MAX_HEADER_BYTES = 65_536
MAX_CONNECTIONS = 64
CONNECT_TIMEOUT = 15.0
# For the whole request head: a per-recv timeout alone lets a local process
# hold every connection slot by trickling bytes.
HEAD_TIMEOUT = 15.0
# A live HLS connection can sit idle between playlist refreshes.
IDLE_TIMEOUT = 120.0
_CHUNK = 65_536
# https://fetch.spec.whatwg.org/#bad-port
BLOCKED_PORTS = frozenset({
    0, 1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79, 87,
    95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137, 139, 143,
    161, 179, 389, 427, 465, 512, 513, 514, 515, 526, 530, 531, 532, 540, 548, 554, 556,
    563, 587, 601, 636, 989, 990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 4190,
    5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080})
_USER = "baihe"
_HOP_HEADERS = {"connection", "keep-alive", "proxy-connection", "proxy-authorization",
                "proxy-authenticate", "te", "trailer", "upgrade"}


class _Refused(Exception):
    def __init__(self, status: str, header: str = ""):
        super().__init__(status)
        self.status = status
        self.header = header


def _read_head(sock, deadline: float = None) -> tuple:
    """(header block bytes without the blank line, bytes read past it).
    deadline: a time.monotonic() value the whole head must arrive by."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEADER_BYTES:
            raise _Refused("431 Request Header Fields Too Large")
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Refused("408 Request Timeout")
            sock.settimeout(remaining)
        chunk = sock.recv(_CHUNK)
        if not chunk:
            raise _Refused("")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def _open_upstream(host: str, port: int):
    """A socket connected to the validated public address of host:port."""
    if not host or not 0 < port < 65536:
        raise _Refused("400 Bad Request")
    if port in BLOCKED_PORTS:
        raise _Refused("403 Forbidden")
    netloc = f"[{host}]" if ":" in host else host
    try:
        ip = url_guard.resolve_public(f"http://{netloc}:{port}/")
    except url_guard.UnsafeURLError:
        raise _Refused("403 Forbidden") from None
    except url_guard.URLResolveError:
        raise _Refused("502 Bad Gateway") from None
    try:
        return socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT)
    except OSError:
        raise _Refused("502 Bad Gateway") from None


def _pump(client, upstream, forward_client: bool):
    """Copy upstream -> client until upstream closes, the client closes
    before sending everything, or both go idle. forward_client: also copy
    client -> upstream (a CONNECT tunnel), passing a client half-close on
    so the response can still arrive."""
    for s in (client, upstream):
        s.settimeout(None)
    socks = [client, upstream]
    while True:
        ready, _, _ = select.select(socks, [], [], IDLE_TIMEOUT)
        if not ready:
            return
        for s in ready:
            data = s.recv(_CHUNK)
            if s is upstream:
                if not data:
                    return
                client.sendall(data)
            elif not data:
                if not forward_client:
                    return
                upstream.shutdown(socket.SHUT_WR)
                socks.remove(client)
            elif forward_client:
                upstream.sendall(data)


def _connect(client, target: str, rest: bytes):
    host, sep, port = target.rpartition(":")
    if not sep or not port.isdigit():
        raise _Refused("400 Bad Request")
    upstream = _open_upstream(host.strip("[]"), int(port))
    try:
        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        if rest:
            upstream.sendall(rest)
        _pump(client, upstream, forward_client=True)
    finally:
        upstream.close()


def _forward(client, method: str, target: str, version: str, header_lines, rest: bytes):
    try:
        parts = urlsplit(target)
        port = parts.port or 80
    except ValueError:
        raise _Refused("400 Bad Request") from None
    if parts.scheme != "http" or not parts.hostname:
        raise _Refused("400 Bad Request")
    headers, length = [], 0
    for line in header_lines:
        name, _, value = line.partition(":")
        key = name.strip().lower()
        if key == "transfer-encoding":
            raise _Refused("501 Not Implemented")
        if key == "content-length":
            if not value.strip().isdigit():
                raise _Refused("400 Bad Request")
            length = int(value.strip())
        if key not in _HOP_HEADERS:
            headers.append(line)
    if not any(h.lower().startswith("host:") for h in headers):
        headers.insert(0, f"Host: {parts.netloc.rpartition('@')[2]}")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    upstream = _open_upstream(parts.hostname, port)
    try:
        request = "\r\n".join([f"{method} {path} {version}", *headers, "Connection: close", "", ""])
        upstream.sendall(request.encode("latin-1") + rest[:length])
        remaining = length - len(rest[:length])
        while remaining > 0:
            data = client.recv(min(_CHUNK, remaining))
            if not data:
                return
            upstream.sendall(data)
            remaining -= len(data)
        # The response says Connection: close too, so the client opens a
        # new connection (and gets a fresh check) for its next request.
        head, body = _read_head(upstream)
        lines = head.decode("latin-1").split("\r\n")
        kept = [lines[0]] + [ln for ln in lines[1:]
                             if ln.partition(":")[0].strip().lower() not in _HOP_HEADERS]
        client.sendall(("\r\n".join(kept + ["Connection: close", "", ""])).encode("latin-1") + body)
        _pump(client, upstream, forward_client=False)
    finally:
        upstream.close()


def _authorized(header_lines, expected: bytes) -> bool:
    for line in header_lines:
        name, _, value = line.partition(":")
        if name.strip().lower() == "proxy-authorization":
            scheme, _, credentials = value.strip().partition(" ")
            if scheme.lower() == "basic" and hmac.compare_digest(
                    credentials.strip().encode("latin-1"), expected):
                return True
    return False


def _handle(client, slots, expected_auth: bytes):
    try:
        head, rest = _read_head(client, time.monotonic() + HEAD_TIMEOUT)
        client.settimeout(CONNECT_TIMEOUT)
        lines = head.decode("latin-1").split("\r\n")
        request_line = lines[0].split(" ")
        if len(request_line) != 3:
            raise _Refused("400 Bad Request")
        if not _authorized(lines[1:], expected_auth):
            # ffmpeg sends credentials only after this challenge.
            raise _Refused("407 Proxy Authentication Required",
                           'Proxy-Authenticate: Basic realm="baihe"\r\n')
        method, target, version = request_line
        if method.upper() == "CONNECT":
            _connect(client, target, rest)
        else:
            _forward(client, method, target, version, lines[1:], rest)
    except _Refused as refusal:
        if refusal.status:
            try:
                client.sendall(f"HTTP/1.1 {refusal.status}\r\n{refusal.header}"
                               "Content-Length: 0\r\nConnection: close\r\n\r\n".encode("latin-1"))
            except OSError:
                pass
    except (OSError, UnicodeError, ValueError):
        pass
    finally:
        client.close()
        slots.release()


class GuardedProxy:
    """`with GuardedProxy() as proxy:` serves on `proxy.url` until the block
    ends. Connections already open are ended when their client closes
    them (ffmpeg is stopped before the proxy). `proxy.url` carries the
    secret in its userinfo: hand it only to the client process, never log
    or store it (translate_engines.redact_secrets masks URL userinfo)."""

    def __init__(self):
        self._listener = None
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._secret = secrets.token_urlsafe(24)
        self._expected_auth = base64.b64encode(f"{_USER}:{self._secret}".encode())
        self.url = None

    def start(self) -> "GuardedProxy":
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.url = f"http://{_USER}:{self._secret}@127.0.0.1:{self._listener.getsockname()[1]}"
        threading.Thread(target=self._serve, args=(self._listener,), daemon=True,
                         name="egress-proxy").start()
        return self

    def _serve(self, listener):
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return  # closed
            if not self._slots.acquire(blocking=False):
                client.close()
                continue
            threading.Thread(target=_handle, args=(client, self._slots, self._expected_auth),
                             daemon=True, name="egress-proxy-conn").start()

    def close(self):
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            listener.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
