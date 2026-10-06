"""services/egress_proxy.py: the loopback proxy live capture runs yt-dlp and
ffmpeg through, so a redirect, a playlist entry or a changed DNS answer
cannot reach a non-public address after the first URL check.

Local servers only. "origin.test" stands for a public host (the resolver is
faked to validate it and pin 127.0.0.1); every other host goes through the
real url_guard, which refuses 127.0.0.1. The ffmpeg tests need a real
ffmpeg binary (and the https one openssl) and are skipped without one."""
import base64
import http.server
import os
import shutil
import ssl
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_translate
import translate_engines
from services import egress_proxy, live_service, url_guard

HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_OPENSSL = shutil.which("openssl") is not None


class _Server:
    """A local HTTP server recording every path it is asked for."""

    def __init__(self, routes=None):
        self.hits = []
        routes = routes or {}
        hits = self.hits

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                status, headers, body = routes.get(self.path, (200, {}, b"secret-body"))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def private_server():
    s = _Server()
    yield s
    s.close()


@pytest.fixture
def proxy(monkeypatch):
    real = url_guard.resolve_public

    def fake_resolve(url):
        if urlsplit(url).hostname == "origin.test":
            return "127.0.0.1"
        return real(url)

    monkeypatch.setattr(url_guard, "resolve_public", fake_resolve)
    with egress_proxy.GuardedProxy() as p:
        yield p


def _auth_header(url: str) -> bytes:
    userinfo = urlsplit(url).netloc.rpartition("@")[0]
    return b"Proxy-Authorization: Basic " + base64.b64encode(userinfo.encode()) + b"\r\n"


def _raw(proxy, request: bytes, then: bytes = b"", auth: bytes = None) -> bytes:
    """Send one request to the proxy and return everything it answers.
    auth: the Proxy-Authorization header line(s), default the proxy's own."""
    line, sep, rest = request.partition(b"\r\n")
    if sep:
        request = line + sep + (_auth_header(proxy.url) if auth is None else auth) + rest
    port = int(proxy.url.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(request)
        if then:
            head = s.recv(4096)
            s.sendall(then)
            s.shutdown(socket.SHUT_WR)
            return head + _read_all(s)
        return _read_all(s)


def _read_all(s) -> bytes:
    """Until the proxy closes, or the Content-Length body is complete (an
    origin that keeps its side open is left to the client to close)."""
    out = b""
    while True:
        head, sep, body = out.partition(b"\r\n\r\n")
        for line in head.split(b"\r\n") if sep else ():
            if line.lower().startswith(b"content-length:") and len(body) >= int(line[15:]):
                return out
        chunk = s.recv(65536)
        if not chunk:
            return out
        out += chunk


def test_public_http_request_is_forwarded_with_connection_close(proxy):
    origin = _Server({"/ok": (200, {"Connection": "keep-alive"}, b"hello")})
    try:
        resp = _raw(proxy, f"GET http://origin.test:{origin.port}/ok HTTP/1.1\r\n"
                           f"Host: origin.test:{origin.port}\r\n"
                           "Proxy-Connection: keep-alive\r\n\r\n".encode())
    finally:
        origin.close()
    head, _, body = resp.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 200") or head.startswith(b"HTTP/1.1 200")
    assert body == b"hello"
    assert b"Connection: close" in head and b"keep-alive" not in head.lower()
    assert origin.hits == ["/ok"]


def test_private_http_target_is_refused_without_connecting(proxy, private_server):
    resp = _raw(proxy, f"GET http://127.0.0.1:{private_server.port}/secret HTTP/1.1\r\n"
                       f"Host: 127.0.0.1\r\n\r\n".encode())
    assert resp.startswith(b"HTTP/1.1 403")
    assert private_server.hits == []


@pytest.mark.parametrize("target", ["127.0.0.1", "[::1]", "localhost", "10.0.0.1",
                                    "169.254.169.254"])
def test_connect_to_a_private_address_is_refused(proxy, target):
    resp = _raw(proxy, f"CONNECT {target}:443 HTTP/1.1\r\nHost: {target}:443\r\n\r\n".encode())
    assert resp.split(b"\r\n")[0] in (b"HTTP/1.1 403 Forbidden", b"HTTP/1.1 502 Bad Gateway")
    assert b"200" not in resp.split(b"\r\n")[0]


def test_connect_to_a_public_host_tunnels_to_the_validated_address(proxy):
    origin = _Server({"/t": (200, {}, b"tunnelled")})
    try:
        resp = _raw(proxy, f"CONNECT origin.test:{origin.port} HTTP/1.1\r\n\r\n".encode(),
                    then=b"GET /t HTTP/1.0\r\nHost: origin.test\r\n\r\n")
    finally:
        origin.close()
    assert resp.startswith(b"HTTP/1.1 200 Connection established")
    assert resp.endswith(b"tunnelled") and origin.hits == ["/t"]


@pytest.mark.parametrize("request_line", [
    "GET /secret HTTP/1.1",                     # origin-form: not a proxy request
    "GET https://origin.test/ HTTP/1.1",        # https must be CONNECT
    "GET ftp://origin.test/ HTTP/1.1",
    "CONNECT origin.test HTTP/1.1",             # no port
    "GARBAGE",
])
def test_malformed_or_non_proxy_requests_are_refused(proxy, request_line):
    resp = _raw(proxy, f"{request_line}\r\nHost: x\r\n\r\n".encode())
    assert resp.startswith(b"HTTP/1.1 400")


def test_chunked_request_body_is_refused(proxy):
    resp = _raw(proxy, b"POST http://origin.test:1/ HTTP/1.1\r\nHost: origin.test\r\n"
                       b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n")
    assert resp.startswith(b"HTTP/1.1 501")


@pytest.mark.parametrize("auth", [
    b"",                                                          # none
    b"Proxy-Authorization: Basic " + base64.b64encode(b"baihe:wrong") + b"\r\n",
    b"Proxy-Authorization: Bearer x\r\n",
])
def test_request_without_the_proxy_secret_is_refused_without_connecting(proxy, auth):
    origin = _Server()
    try:
        get = _raw(proxy, f"GET http://origin.test:{origin.port}/x HTTP/1.1\r\n"
                          "Host: origin.test\r\n\r\n".encode(), auth=auth)
        connect = _raw(proxy, f"CONNECT origin.test:{origin.port} HTTP/1.1\r\n\r\n".encode(),
                       auth=auth)
    finally:
        origin.close()
    for resp in (get, connect):
        assert resp.startswith(b"HTTP/1.1 407")
        assert b"Proxy-Authenticate: Basic" in resp
    assert origin.hits == []


def test_each_proxy_has_its_own_secret_and_redaction_hides_it():
    with egress_proxy.GuardedProxy() as a, egress_proxy.GuardedProxy() as b:
        secret = urlsplit(a.url).password
        assert secret and secret != urlsplit(b.url).password
        shown = translate_engines.redact_secrets(f"Unable to connect to proxy {a.url}")
        assert secret not in shown
        assert secret not in live_service.clean_message(f"ProxyError: {a.url} refused")


@pytest.mark.parametrize("port", [22, 25, 6667])
def test_browser_blocked_ports_are_refused_on_a_public_host(proxy, port):
    for request in (f"CONNECT origin.test:{port} HTTP/1.1\r\n\r\n",
                    f"GET http://origin.test:{port}/ HTTP/1.1\r\nHost: origin.test\r\n\r\n"):
        assert _raw(proxy, request.encode()).startswith(b"HTTP/1.1 403")
    # ordinary media ports are not on the list
    assert not {80, 443, 1935, 8000, 8080, 8443} & egress_proxy.BLOCKED_PORTS


def test_closed_proxy_refuses_new_connections():
    p = egress_proxy.GuardedProxy().start()
    port = int(p.url.rsplit(":", 1)[1])
    p.close()
    with pytest.raises(OSError), socket.socket() as s:
        s.connect(("127.0.0.1", port))


def test_capture_env_routes_ffmpeg_through_the_proxy(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("https_proxy", "http://corp.example:3128")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, **kw))
    live_translate.start_segment_capture("https://cdn.example/x.m3u8", str(tmp_path),
                                         protocol_whitelist="http", proxy="http://127.0.0.1:9")
    env = seen["env"]
    assert env["http_proxy"] == "http://127.0.0.1:9"
    assert not {"no_proxy", "https_proxy"} & {k.lower() for k in env}


def test_capture_without_proxy_keeps_the_inherited_environment(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, **kw))
    live_translate.start_segment_capture("https://cdn.example/x.m3u8", str(tmp_path))
    assert seen["env"] is None


def _run_ffmpeg_through(proxy, url, tmp_path):
    proc = live_translate.start_segment_capture(
        url, str(tmp_path / "chunks"), segment_seconds=10,
        protocol_whitelist=live_service.FFMPEG_PROTOCOL_WHITELIST, proxy=proxy.url)
    try:
        proc.wait(timeout=30)
    finally:
        live_translate.stop_capture(proc)


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
def test_ffmpeg_redirect_to_a_private_host_is_not_followed(proxy, private_server, tmp_path):
    origin = _Server({"/live": (302, {"Location":
                                      f"http://127.0.0.1:{private_server.port}/secret"}, b"")})
    try:
        _run_ffmpeg_through(proxy, f"http://origin.test:{origin.port}/live", tmp_path)
    finally:
        origin.close()
    assert origin.hits == ["/live"]
    assert private_server.hits == []


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
def test_ffmpeg_hls_segments_and_keys_on_a_private_host_are_not_fetched(
        proxy, private_server, tmp_path):
    private = f"http://127.0.0.1:{private_server.port}"
    playlist = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
                f'#EXT-X-KEY:METHOD=AES-128,URI="{private}/key"\n'
                f"#EXTINF:2.0,\n{private}/seg0.ts\n#EXT-X-ENDLIST\n").encode()
    origin = _Server({"/live.m3u8": (200, {"Content-Type": "application/vnd.apple.mpegurl"},
                                     playlist)})
    try:
        _run_ffmpeg_through(proxy, f"http://origin.test:{origin.port}/live.m3u8", tmp_path)
    finally:
        origin.close()
    assert "/live.m3u8" in origin.hits
    assert private_server.hits == []


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
def test_ffmpeg_reads_a_public_source_through_the_proxy(proxy, tmp_path):
    """Normal behaviour is kept: a public stream is read (ffmpeg never
    resolves origin.test itself, so the bytes can only come via the proxy)."""
    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=3",
                    "-ac", "1", "-ar", "16000", str(src)], check=True, timeout=30)
    origin = _Server({"/tone.wav": (200, {"Content-Type": "audio/wav"}, src.read_bytes())})
    try:
        _run_ffmpeg_through(proxy, f"http://origin.test:{origin.port}/tone.wav", tmp_path)
    finally:
        origin.close()
    assert origin.hits == ["/tone.wav"]
    assert os.listdir(tmp_path / "chunks")  # audio arrived and was segmented


class _Sentinel:
    """A raw TCP listener on loopback that records every connection."""

    def __init__(self):
        self.hits = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.hits.append(1)
            conn.close()

    def close(self):
        self.sock.close()


def _hls(entry: str) -> bytes:
    return ("#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\n"
            f"{entry}\n#EXT-X-ENDLIST\n").encode()


_TCP_ENTRIES = {
    "hls_segment": lambda p: {"/s": (200, {}, _hls(f"tcp://127.0.0.1:{p}/seg.ts"))},
    "hls_variant": lambda p: {"/s": (200, {}, (
        f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\ntcp://127.0.0.1:{p}/v.m3u8\n").encode())},
    "hls_key": lambda p: {"/s": (200, {}, (
        f'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-KEY:METHOD=AES-128,URI="tcp://127.0.0.1:{p}/k"'
        "\n#EXTINF:2,\n/seg.ts\n#EXT-X-ENDLIST\n").encode()),
        "/seg.ts": (200, {}, b"\x47" * 1880)},
    "hls_crypto_segment": lambda p: {"/s": (200, {}, _hls(f"crypto+tcp://127.0.0.1:{p}/seg.ts"))},
    "dash_base_url": lambda p: {"/s": (200, {"Content-Type": "application/dash+xml"}, (
        '<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" '
        'mediaPresentationDuration="PT2S" minBufferTime="PT1S" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011"><Period>'
        '<AdaptationSet mimeType="audio/mp4"><Representation id="a" bandwidth="1">'
        f"<BaseURL>tcp://127.0.0.1:{p}/a.mp4</BaseURL></Representation></AdaptationSet>"
        "</Period></MPD>").encode())},
    "ffconcat": lambda p: {"/s": (200, {}, (
        f"ffconcat version 1.0\nfile 'tcp://127.0.0.1:{p}/'\n").encode())},
    "redirect": lambda p: {"/s": (302, {"Location": f"tcp://127.0.0.1:{p}/"}, b"")},
}


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
@pytest.mark.parametrize("kind", sorted(_TCP_ENTRIES))
def test_ffmpeg_never_opens_a_tcp_url_a_stream_names(proxy, tmp_path, kind):
    """tcp must stay whitelisted (http, tls and httpproxy run over it), so a
    raw tcp:// URL would skip the proxy. ffmpeg's HLS and DASH demuxers only
    open http(s) entries, concat refuses URLs in safe mode, and a redirect
    reaches the proxy as an absolute-form non-http request, which it
    refuses; this pins that for every place a stream can name one."""
    sentinel = _Sentinel()
    origin = _Server(_TCP_ENTRIES[kind](sentinel.port))
    try:
        _run_ffmpeg_through(proxy, f"http://origin.test:{origin.port}/s", tmp_path)
    finally:
        origin.close()
        sentinel.close()
    assert origin.hits[:1] == ["/s"]
    assert sentinel.hits == []


@pytest.mark.skipif(not (HAS_FFMPEG and HAS_OPENSSL), reason="needs real ffmpeg and openssl")
def test_ffmpeg_reads_https_through_the_proxy_with_its_secret(proxy, tmp_path):
    """https goes as an authenticated CONNECT (ffmpeg answers the 407
    challenge with the userinfo of http_proxy)."""
    key, cert, src = tmp_path / "k.pem", tmp_path / "c.pem", tmp_path / "tone.wav"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout",
                    str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=origin.test"],
                   check=True, capture_output=True, timeout=60)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=3",
                    "-ac", "1", "-ar", "16000", str(src)], check=True, timeout=30)
    origin = _Server({"/tone.wav": (200, {"Content-Type": "audio/wav"}, src.read_bytes())})
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    origin.httpd.socket = ctx.wrap_socket(origin.httpd.socket, server_side=True)
    try:
        _run_ffmpeg_through(proxy, f"https://origin.test:{origin.port}/tone.wav", tmp_path)
    finally:
        origin.close()
    assert origin.hits == ["/tone.wav"]
    assert os.listdir(tmp_path / "chunks")
