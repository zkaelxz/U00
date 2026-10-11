"""services/egress_proxy.py: the loopback proxy live capture runs yt-dlp and
its stream fetcher through, so a redirect, a playlist entry or a changed
DNS answer cannot reach a non-public address after the first URL check;
and the capture as a whole: ffmpeg reads only the piped stream.

Local servers only. "origin.test" stands for a public host (the resolver is
faked to validate it and pin 127.0.0.1); every other host goes through the
real url_guard, which refuses 127.0.0.1. The capture tests need a real
ffmpeg binary (and the https ones openssl) and are skipped without one."""
import base64
import http.server
import os
import shutil
import ssl
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_fetch
import live_translate
import translate_engines
from services import egress_proxy, live_service
from lib import url_guard

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


def test_capture_ffmpeg_opens_only_its_stdin(monkeypatch, tmp_path):
    """ffmpeg gets no URL and no proxy: it reads the stream live_fetch
    pipes to it, with every protocol but `pipe` refused."""
    seen = {}

    class _Proc:
        stdin = None

        def kill(self):
            pass

        def wait(self):
            pass

    monkeypatch.setenv("http_proxy", "http://corp.example:3128")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, **kw) or _Proc())
    monkeypatch.setattr(live_fetch.StreamPump, "start", lambda self: self)
    capture = live_translate.start_segment_capture("https://cdn.example/x.m3u8", str(tmp_path),
                                                   proxy="http://u:p@127.0.0.1:9")
    cmd = seen["cmd"]
    i = cmd.index("-protocol_whitelist")
    assert cmd[i + 1] == "pipe" and cmd[cmd.index("-i") + 1] == "pipe:0" and i < cmd.index("-i")
    assert cmd[cmd.index("-format_whitelist") + 1] == live_translate.FFMPEG_FORMAT_WHITELIST
    assert not any("cdn.example" in arg or "127.0.0.1" in arg for arg in cmd)
    assert seen["stdin"] == subprocess.PIPE and seen.get("env") is None
    session = capture.pump._session
    assert session.trust_env is False  # no no_proxy / corporate proxy from the environment
    assert session.proxies == {"http": "http://u:p@127.0.0.1:9", "https": "http://u:p@127.0.0.1:9"}


def _capture(proxy, url, tmp_path, timeout=30):
    """Runs a real capture until ffmpeg ends; returns the capture."""
    capture = live_translate.start_segment_capture(url, str(tmp_path / "chunks"), 10,
                                                   proxy=proxy.url)
    try:
        deadline = time.monotonic() + timeout
        while capture.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        live_translate.stop_capture(capture)
    assert not capture.pump.alive()
    return capture


def _chunks(tmp_path):
    out = tmp_path / "chunks"
    return os.listdir(out) if out.exists() else []


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
def test_redirect_to_a_private_host_is_not_followed(proxy, private_server, tmp_path):
    origin = _Server({"/live": (302, {"Location":
                                      f"http://127.0.0.1:{private_server.port}/secret"}, b"")})
    try:
        capture = _capture(proxy, f"http://origin.test:{origin.port}/live", tmp_path)
    finally:
        origin.close()
    assert origin.hits == ["/live"]
    assert private_server.hits == []
    assert capture.error == live_fetch.REFUSED


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
def test_hls_segments_and_keys_on_a_private_host_are_not_fetched(proxy, private_server, tmp_path):
    private = f"http://127.0.0.1:{private_server.port}"
    playlist = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
                f'#EXT-X-KEY:METHOD=AES-128,URI="{private}/key"\n'
                f"#EXTINF:2.0,\n{private}/seg0.ts\n#EXT-X-ENDLIST\n").encode()
    origin = _Server({"/live.m3u8": (200, _M3U8, playlist)})
    try:
        _capture(proxy, f"http://origin.test:{origin.port}/live.m3u8", tmp_path)
    finally:
        origin.close()
    assert origin.hits == ["/live.m3u8"]
    assert private_server.hits == []


def _tone(tmp_path, seconds=3):
    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=d={seconds}",
                    "-ac", "1", "-ar", "16000", str(src)], check=True, timeout=30)
    return src


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
def test_a_public_direct_stream_is_piped_to_ffmpeg_through_the_proxy(proxy, tmp_path):
    """Normal behaviour is kept: a public stream is read (nothing resolves
    origin.test but the test's proxy resolver, so the bytes can only come
    via the proxy)."""
    origin = _Server({"/tone.wav": (200, {"Content-Type": "audio/wav"},
                                    _tone(tmp_path).read_bytes())})
    try:
        capture = _capture(proxy, f"http://origin.test:{origin.port}/tone.wav", tmp_path)
    finally:
        origin.close()
    assert origin.hits == ["/tone.wav"]
    assert capture.error is None and capture.poll() == 0
    assert _chunks(tmp_path)  # audio arrived and was segmented


class _Sentinel:
    """A raw TCP listener on loopback that records every connection made to
    it (and what it sends, up to the end of a request head)."""

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
            # Recorded at accept, so a test asserting right after close()
            # can't miss a connection whose request was still being read.
            index = len(self.hits)
            self.hits.append(b"")
            conn.settimeout(2)
            data = b""
            try:
                while b"\r\n\r\n" not in data and len(data) < 65536:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            except OSError:
                pass
            self.hits[index] = data
            conn.close()

    def close(self):
        self.sock.close()


# Without it a playlist at a path not ending in .m3u8 isn't taken for HLS.
_M3U8 = {"Content-Type": "application/vnd.apple.mpegurl"}
_SEG = (200, {}, b"\x47" * 1880)


def _variant(uri: str) -> bytes:
    return f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n{uri}\n".encode()


def _segment(uri: str) -> bytes:
    return f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\n{uri}\n#EXT-X-ENDLIST\n".encode()


def _key(uri: str) -> bytes:
    return ("#EXTM3U\n#EXT-X-TARGETDURATION:2\n"
            f'#EXT-X-KEY:METHOD=AES-128,URI="{uri}"\n'
            "#EXTINF:2,\n/seg.ts\n#EXT-X-ENDLIST\n").encode()


def _audio_rendition(uri: str) -> bytes:
    return ('#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="x",DEFAULT=YES,'
            f'URI="{uri}"\n#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="a"\n/v.m3u8\n').encode()


def _map(uri: str) -> bytes:
    return (f'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MAP:URI="{uri}"\n'
            "#EXTINF:2,\n/seg.ts\n#EXT-X-ENDLIST\n").encode()


_U = "urn:uuid:"
_IMF_CPL = (
    '<?xml version="1.0"?><CompositionPlaylist xmlns="http://www.smpte-ra.org/schemas/2067-3/2016"'
    ' xmlns:cc="http://www.smpte-ra.org/schemas/2067-2/2016"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    f"<Id>{_U}11111111-1111-1111-1111-111111111111</Id><ContentTitle>t</ContentTitle>"
    f"<EditRate>24 1</EditRate><SegmentList><Segment><Id>{_U}22222222-2222-2222-2222-222222222222"
    f"</Id><SequenceList><cc:MainAudioSequence><Id>{_U}33333333-3333-3333-3333-333333333333</Id>"
    f"<TrackId>{_U}44444444-4444-4444-4444-444444444444</TrackId><ResourceList>"
    f'<Resource xsi:type="TrackFileResourceType"><Id>{_U}55555555-5555-5555-5555-555555555555'
    "</Id><EditRate>24 1</EditRate><IntrinsicDuration>24</IntrinsicDuration>"
    f"<TrackFileId>{_U}66666666-6666-6666-6666-666666666666</TrackFileId></Resource>"
    "</ResourceList></cc:MainAudioSequence></SequenceList></Segment></SegmentList>"
    "</CompositionPlaylist>")
_IMF_ASSETMAP = (
    '<?xml version="1.0"?><AssetMap xmlns="http://www.smpte-ra.org/schemas/429-9/2007/AM">'
    f"<Id>{_U}77777777-7777-7777-7777-777777777777</Id><AssetList><Asset>"
    f"<Id>{_U}66666666-6666-6666-6666-666666666666</Id><ChunkList><Chunk><Path>{{path}}</Path>"
    "</Chunk></ChunkList></Asset></AssetList></AssetMap>")


def _mpd(base_url: str) -> bytes:
    return ('<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" '
            'mediaPresentationDuration="PT2S" minBufferTime="PT1S" '
            'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011"><Period>'
            '<AdaptationSet mimeType="audio/mp4"><Representation id="a" bandwidth="1">'
            f"<BaseURL>{base_url}</BaseURL></Representation></AdaptationSet>"
            "</Period></MPD>").encode()


def _crlf_base_url(scheme: str, port: int) -> str:
    # &#13;&#10; decodes to a real CR LF in an XML reader.
    return (f"{scheme}://127.0.0.1:{port}/x&#13;&#10;X-Injected: 1&#13;&#10;"
            "&#13;&#10;GET /injected HTTP/1.1")


# Every way a stream can name a connection of its own, with the sentinel
# as the target. Before the stream was piped, ffmpeg opened the tcp://
# variant and every httpproxy:// entry itself, bypassing the proxy.
_SCHEMES = {
    "tcp": lambda p: f"tcp://127.0.0.1:{p}/x",
    "httpproxy": lambda p: f"httpproxy://127.0.0.1:{p}/x",
    "crypto_httpproxy": lambda p: f"crypto+httpproxy://127.0.0.1:{p}/x",
    "private_http": lambda p: f"http://127.0.0.1:{p}/x",
    "private_https": lambda p: f"https://127.0.0.1:{p}/x",
}
_POSITIONS = {"variant": _variant, "segment": _segment, "key": _key,
              "audio_rendition": _audio_rendition, "init_map": _map}
_ENTRIES = {f"{pos}_{scheme}": (lambda p, pos=pos, scheme=scheme: {
    "/s": (200, _M3U8, _POSITIONS[pos](_SCHEMES[scheme](p))), "/seg.ts": _SEG})
    for pos in _POSITIONS for scheme in _SCHEMES}
_ENTRIES.update({
    f"dash_crlf_base_url_{scheme}": (lambda p, scheme=scheme: {
        "/s": (200, {"Content-Type": "application/dash+xml"}, _mpd(_crlf_base_url(scheme, p)))})
    for scheme in ("http", "httpproxy", "tcp")})
_ENTRIES.update({
    "dash_tcp_base_url": lambda p: {"/s": (200, {"Content-Type": "application/dash+xml"},
                                           _mpd(f"tcp://127.0.0.1:{p}/a.mp4"))},
    "imf_asset_path": lambda p: {
        "/s": (200, {}, _IMF_CPL.encode()),
        "/ASSETMAP.xml": (200, {}, _IMF_ASSETMAP.format(path=f"tcp://127.0.0.1:{p}/").encode())},
    "ffconcat": lambda p: {"/s": (200, {}, (
        f"ffconcat version 1.0\nfile 'tcp://127.0.0.1:{p}/'\n").encode())},
    "redirect_tcp": lambda p: {"/s": (302, {"Location": f"tcp://127.0.0.1:{p}/"}, b"")},
    "redirect_httpproxy": lambda p: {"/s": (302, {"Location": f"httpproxy://127.0.0.1:{p}/x"},
                                            b"")},
})


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
@pytest.mark.parametrize("kind", sorted(_ENTRIES))
def test_nothing_a_stream_names_reaches_a_local_port(proxy, tmp_path, kind):
    """The full capture (fetcher, proxy and a real ffmpeg reading its
    stdin): the sentinel on loopback gets no connection at all."""
    sentinel = _Sentinel()
    origin = _Server(_ENTRIES[kind](sentinel.port))
    try:
        capture = _capture(proxy, f"http://origin.test:{origin.port}/s", tmp_path)
    finally:
        origin.close()
        sentinel.close()
    assert origin.hits[:1] == ["/s"]
    assert sentinel.hits == []
    assert capture.error or capture.poll() != 0 or not _chunks(tmp_path)


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
@pytest.mark.parametrize("position", ["variant", "segment", "key"])
def test_a_file_url_in_a_playlist_is_never_read(proxy, tmp_path, position):
    secret = _tone(tmp_path)
    origin = _Server({"/s": (200, _M3U8, _POSITIONS[position](secret.as_uri())),
                      "/seg.ts": _SEG})
    try:
        capture = _capture(proxy, f"http://origin.test:{origin.port}/s", tmp_path)
    finally:
        origin.close()
    assert capture.error == live_fetch.NOT_HTTP
    assert not _chunks(tmp_path)


def _ffmpeg_has_demuxer(name: str) -> bool:
    if not HAS_FFMPEG:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-demuxers"], capture_output=True,
                         text=True, timeout=30).stdout
    return any(line.split()[1:2] == [name] for line in out.splitlines())


def _capture_cmd(monkeypatch, tmp_path) -> list:
    """The ffmpeg command start_segment_capture runs."""
    seen = {}

    class _Proc:
        stdin = None

    real = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd) or _Proc())
    monkeypatch.setattr(live_fetch.StreamPump, "start", lambda self: self)
    live_translate.start_segment_capture("http://x.example/", str(tmp_path / "chunks"), 10)
    monkeypatch.setattr(subprocess, "Popen", real)
    return seen["cmd"]


@pytest.mark.skipif(not _ffmpeg_has_demuxer("hls"),
                    reason="needs a real ffmpeg binary with the hls demuxer")
@pytest.mark.parametrize("position", ["variant", "segment", "key"])
@pytest.mark.parametrize("scheme", ["http", "tcp", "httpproxy", "crypto+httpproxy"])
def test_ffmpeg_on_a_pipe_opens_nothing_even_with_the_hls_demuxer(position, scheme):
    """Without the format whitelist: ffmpeg forced to read a piped HLS
    playlist still connects nowhere, as `-protocol_whitelist pipe` refuses
    every protocol an entry could name."""
    sentinel = _Sentinel()
    data = _POSITIONS[position](f"{scheme}://127.0.0.1:{sentinel.port}/x")
    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-protocol_whitelist", "pipe",
                                 "-f", "hls", "-i", "pipe:0", "-f", "null", "-"],
                                input=data, capture_output=True, timeout=30)
    finally:
        sentinel.close()
    assert sentinel.hits == []
    assert result.returncode != 0


@pytest.mark.skipif(not _ffmpeg_has_demuxer("dash"),
                    reason="needs a real ffmpeg binary with the dash demuxer")
@pytest.mark.parametrize("scheme", ["http", "httpproxy"])
def test_piped_dash_is_refused_by_the_format_whitelist(monkeypatch, tmp_path, scheme):
    """ffmpeg 6.1's DASH demuxer opens http and httpproxy fragment URLs
    even under `-protocol_whitelist pipe` (checked when this was written),
    so the format whitelist is what keeps a piped manifest unread."""
    sentinel = _Sentinel()
    data = _mpd(_crlf_base_url(scheme, sentinel.port))
    try:
        result = subprocess.run(_capture_cmd(monkeypatch, tmp_path), input=data,
                                capture_output=True, timeout=30)
    finally:
        sentinel.close()
    assert sentinel.hits == []
    assert b"Format not on whitelist" in result.stderr
    assert result.returncode != 0


def _serve_tls(origin, tmp_path, monkeypatch):
    """Switch a _Server to https with a throwaway origin.test certificate,
    which the fetcher is told to trust."""
    key, cert = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout",
                    str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=origin.test",
                    "-addext", "subjectAltName=DNS:origin.test"],
                   check=True, capture_output=True, timeout=60)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    origin.httpd.socket = ctx.wrap_socket(origin.httpd.socket, server_side=True)
    monkeypatch.setattr(live_fetch, "TLS_VERIFY", str(cert))


@pytest.mark.skipif(not (HAS_FFMPEG and HAS_OPENSSL), reason="needs real ffmpeg and openssl")
def test_https_goes_through_the_proxy_with_its_secret(proxy, tmp_path, monkeypatch):
    """https goes as an authenticated CONNECT, and the certificate is
    verified (here against the test's own CA)."""
    origin = _Server({"/tone.wav": (200, {"Content-Type": "audio/wav"},
                                    _tone(tmp_path).read_bytes())})
    _serve_tls(origin, tmp_path, monkeypatch)
    try:
        capture = _capture(proxy, f"https://origin.test:{origin.port}/tone.wav", tmp_path)
    finally:
        origin.close()
    assert origin.hits == ["/tone.wav"]
    assert capture.error is None
    assert _chunks(tmp_path)


@pytest.mark.skipif(not (HAS_FFMPEG and HAS_OPENSSL), reason="needs real ffmpeg and openssl")
def test_https_with_an_untrusted_certificate_is_refused(proxy, tmp_path, monkeypatch):
    origin = _Server({"/tone.wav": (200, {}, b"x")})
    _serve_tls(origin, tmp_path, monkeypatch)
    monkeypatch.setattr(live_fetch, "TLS_VERIFY", True)
    try:
        capture = _capture(proxy, f"https://origin.test:{origin.port}/tone.wav", tmp_path)
    finally:
        origin.close()
    assert origin.hits == []
    assert capture.error == live_fetch.UNREACHABLE


class _HangingServer(_Server):
    """Sends a WAV header and a little audio, then holds the response open."""

    def __init__(self, head: bytes):
        super().__init__()
        self.closed = threading.Event()
        closed = self.closed

        def do_get(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "audio/wav")
            handler.end_headers()
            handler.wfile.write(head)
            handler.wfile.flush()
            handler.connection.settimeout(30)
            if _until_peer_closes(handler.connection):
                closed.set()

        self.httpd.RequestHandlerClass.do_GET = do_get


@pytest.mark.skipif(not (HAS_FFMPEG and HAS_OPENSSL), reason="needs real ffmpeg and openssl")
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_stop_ends_a_capture_blocked_on_a_silent_stream_promptly(proxy, tmp_path, monkeypatch,
                                                                 scheme):
    """stop_capture wakes the fetch blocked reading through the proxy
    tunnel, ends ffmpeg and leaves no fetcher thread or open upstream
    connection behind."""
    origin = _HangingServer(_tone(tmp_path, seconds=1).read_bytes())
    if scheme == "https":
        _serve_tls(origin, tmp_path, monkeypatch)
    capture = live_translate.start_segment_capture(f"{scheme}://origin.test:{origin.port}/a",
                                                   str(tmp_path / "chunks"), 10, proxy=proxy.url)
    try:
        time.sleep(1.0)
        assert capture.poll() is None and capture.error is None
        started = time.monotonic()
        live_translate.stop_capture(capture)
        assert time.monotonic() - started < 5
        assert capture.poll() is not None and not capture.pump.alive()
        assert origin.closed.wait(5)
    finally:
        live_translate.stop_capture(capture)
        origin.close()


@pytest.mark.skipif(not (HAS_FFMPEG and HAS_OPENSSL), reason="needs real ffmpeg and openssl")
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("segment_type", ["mpegts", "fmp4"])
def test_hls_is_fetched_through_the_proxy_and_piped_to_ffmpeg(
        proxy, tmp_path, monkeypatch, scheme, segment_type):
    """An ordinary HLS stream, with MPEG-TS or fragmented-MP4 segments,
    still becomes audio chunks within the format whitelist."""
    hls_dir = tmp_path / "hls"
    hls_dir.mkdir()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=4",
                    "-c:a", "aac", "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod",
                    "-hls_segment_type", segment_type, str(hls_dir / "live.m3u8")],
                   check=True, timeout=60)
    routes = {f"/{f.name}": (200, _M3U8 if f.suffix == ".m3u8" else {}, f.read_bytes())
              for f in hls_dir.iterdir()}
    origin = _Server(routes)
    if scheme == "https":
        _serve_tls(origin, tmp_path, monkeypatch)
    try:
        capture = _capture(proxy, f"{scheme}://origin.test:{origin.port}/live.m3u8", tmp_path)
    finally:
        origin.close()
    assert set(origin.hits) == set(routes)  # playlist, every segment (and the init)
    assert capture.error is None and capture.poll() == 0
    assert _chunks(tmp_path)


def test_request_head_must_arrive_within_the_overall_deadline(proxy, monkeypatch):
    """Trickling one byte at a time (each well inside the per-recv timeout)
    must not hold a connection slot past HEAD_TIMEOUT."""
    monkeypatch.setattr(egress_proxy, "HEAD_TIMEOUT", 0.5)
    port = int(proxy.url.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        started = time.monotonic()
        closed = False
        for byte in b"CONNECT origin.test:443 HTTP/1.1\r\nX-Slow: " + b"a" * 200:
            try:
                s.sendall(bytes([byte]))
            except OSError:
                closed = True
                break
            s.settimeout(0.05)
            try:
                if s.recv(4096) == b"":
                    closed = True
                    break
            except socket.timeout:
                pass
            except OSError:
                closed = True
                break
        assert closed
        assert time.monotonic() - started < 3



def _until_peer_closes(conn) -> bool:
    """Reads until the other side closes (True), or until conn's timeout
    expires (False: still open, so not a close)."""
    try:
        while conn.recv(4096):
            pass
        return True
    except ConnectionResetError:
        return True
    except OSError:
        return False


# Response heads that never finish: an HTTP status line and header, and
# a TLS record (a ServerHello's) announcing 16 KiB that never arrive.
_HTTP_HEAD = b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 500
_TLS_HEAD = b"\x16\x03\x03\x40\x00\x02" + b"\0" * 500


class _SilentOrigin:
    """A raw origin on loopback that reads the request and never answers
    (or sends `trickle` one byte at a time, never finishing a response
    head); records when the other side closes."""

    def __init__(self, trickle: bytes = b""):
        self.accepted = threading.Event()
        self.closed_by_peer = threading.Event()
        self.trickle = trickle
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        self.accepted.set()
        conn.settimeout(10)
        closed = False
        if self.trickle:
            try:
                conn.recv(4096)
                for byte in self.trickle:
                    conn.sendall(bytes([byte]))
                    time.sleep(0.05)
            except (ConnectionResetError, BrokenPipeError):
                closed = True
            except OSError:
                pass
        if closed or _until_peer_closes(conn):
            self.closed_by_peer.set()
        conn.close()

    def close(self):
        self.sock.close()


def _proxy_threads():
    return {t for t in threading.enumerate() if t.name.startswith("egress-proxy")}


def _wait_threads_gone(threads, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return not any(t.is_alive() for t in threads)


@pytest.mark.parametrize("method", ["GET", "CONNECT"])
def test_close_ends_connections_waiting_on_a_stalled_upstream(monkeypatch, method):
    """close() shuts down every client and upstream socket still open,
    so no connection thread outlives the session."""
    monkeypatch.setattr(url_guard, "resolve_public", lambda url: "127.0.0.1")
    origin = _SilentOrigin()
    before = _proxy_threads()
    p = egress_proxy.GuardedProxy().start()
    port = int(p.url.rsplit(":", 1)[1])
    target = (f"GET http://origin.test:{origin.port}/ HTTP/1.1\r\nHost: origin.test\r\n"
              if method == "GET" else f"CONNECT origin.test:{origin.port} HTTP/1.1\r\n")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(target.encode() + _auth_header(p.url) + b"\r\n")
            if method == "CONNECT":
                assert s.recv(4096).startswith(b"HTTP/1.1 200")
                s.sendall(b"GET / HTTP/1.1\r\nHost: origin.test\r\n\r\n")
            assert origin.accepted.wait(5)
            started = time.monotonic()
            p.close()
            assert _read_all(s) == b""  # ended, not answered
            assert time.monotonic() - started < 3
        assert origin.closed_by_peer.wait(5)
        assert _wait_threads_gone(_proxy_threads() - before)
    finally:
        p.close()
        origin.close()


def test_upstream_response_head_must_arrive_within_the_overall_deadline(proxy, monkeypatch):
    monkeypatch.setattr(egress_proxy, "UPSTREAM_HEAD_TIMEOUT", 0.5)
    origin = _SilentOrigin(trickle=_HTTP_HEAD)
    try:
        started = time.monotonic()
        answer = _raw(proxy, f"GET http://origin.test:{origin.port}/ HTTP/1.1\r\n"
                             "Host: origin.test\r\n\r\n".encode())
        assert answer.startswith(b"HTTP/1.1 504")
        assert time.monotonic() - started < 3
        assert origin.closed_by_peer.wait(5)
    finally:
        origin.close()


class _NoAnswerServer(_Server):
    """Takes the request (over TLS too, once _serve_tls wraps it) and
    never answers it."""

    def __init__(self):
        super().__init__()
        self.accepted = threading.Event()
        self.closed_by_peer = threading.Event()
        server = self

        def do_get(handler):
            server.accepted.set()
            handler.connection.settimeout(10)
            if _until_peer_closes(handler.connection):
                server.closed_by_peer.set()

        self.httpd.RequestHandlerClass.do_GET = do_get


@pytest.mark.parametrize("scheme,stage", [
    ("http", "headers"), ("https", "tls_handshake"),
    pytest.param("https", "headers", marks=pytest.mark.skipif(
        not HAS_OPENSSL, reason="needs openssl"))])
def test_halt_while_waiting_for_headers_through_the_proxy_leaves_nothing_open(
        proxy, tmp_path, monkeypatch, scheme, stage):
    """The fetch is still inside session.get (no response yet): halt()
    alone ends it, and closing the proxy afterwards leaves no proxy
    thread or upstream connection behind."""
    if scheme == "https" and stage == "headers":
        origin = _NoAnswerServer()
        _serve_tls(origin, tmp_path, monkeypatch)
    else:
        origin = _SilentOrigin()
    before = _proxy_threads()
    sink_closed = threading.Event()

    class Sink:
        def write(self, b):
            pass

        def flush(self):
            pass

        def close(self):
            sink_closed.set()

    pump = live_fetch.StreamPump(f"{scheme}://origin.test:{origin.port}/a", Sink(),
                                 proxy=proxy.url).start()
    try:
        assert origin.accepted.wait(5)
        time.sleep(0.2)
        started = time.monotonic()
        pump.halt()
        pump.join(5)
        assert time.monotonic() - started < 3
        assert not pump.alive() and sink_closed.is_set() and pump.error is None
        proxy.close()
        assert origin.closed_by_peer.wait(5)
        assert _wait_threads_gone(_proxy_threads() - before)
        assert not [t for t in threading.enumerate() if t.name in ("live-fetch", "live-pipe")]
    finally:
        pump.halt()
        origin.close()


@pytest.mark.parametrize("scheme,head", [("http", _HTTP_HEAD), ("https", _TLS_HEAD)],
                         ids=["http-headers", "https-handshake"])
def test_a_request_whose_handshake_or_headers_trickle_ends_at_the_deadline(proxy, scheme,
                                                                           head):
    """Every byte arrives well inside READ_TIMEOUT and, over https, inside
    a CONNECT tunnel the proxy's head deadline does not cover: the
    fetcher's own deadline still ends the request."""
    origin = _SilentOrigin(trickle=head)
    sink_closed = threading.Event()

    class Sink:
        def write(self, b):
            pass

        def flush(self):
            pass

        def close(self):
            sink_closed.set()

    started = time.monotonic()
    pump = live_fetch.StreamPump(f"{scheme}://origin.test:{origin.port}/a", Sink(),
                                 proxy=proxy.url, stall_timeout=0.5).start()
    try:
        assert sink_closed.wait(10)
        pump.join(5)
        assert time.monotonic() - started < 4
        assert not pump.alive() and pump.error == live_fetch.STALLED
        # A plain-http forward waits out its own head deadline; the
        # capture's end closes the proxy, which ends it.
        proxy.close()
        assert origin.closed_by_peer.wait(5)
    finally:
        pump.halt()
        origin.close()
