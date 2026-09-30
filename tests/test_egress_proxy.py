"""services/egress_proxy.py: the loopback proxy live capture runs yt-dlp and
ffmpeg through, so a redirect, a playlist entry or a changed DNS answer
cannot reach a non-public address after the first URL check.

Local servers only. "origin.test" stands for a public host (the resolver is
faked to validate it and pin 127.0.0.1); every other host goes through the
real url_guard, which refuses 127.0.0.1. The ffmpeg tests need a real
ffmpeg binary and are skipped without one."""
import http.server
import os
import shutil
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_translate
from services import egress_proxy, live_service, url_guard

HAS_FFMPEG = shutil.which("ffmpeg") is not None


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


def _raw(proxy, request: bytes, then: bytes = b"") -> bytes:
    """Send one request to the proxy and return everything it answers."""
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
