"""live_fetch.py: the fetcher that feeds live capture's ffmpeg through a
pipe. Loopback HTTP servers and a fake sink stand in for the stream host
and ffmpeg; no proxy here (tests/test_egress_proxy.py runs the fetcher
through the proxy into a real ffmpeg)."""
import http.server
import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_fetch

_M3U8 = {"Content-Type": "application/vnd.apple.mpegurl"}


class _Server:
    """Records each path asked for. A route is (status, headers, body) or a
    callable returning one; a body may be a callable writing to the
    handler itself (to stall mid-response)."""

    def __init__(self, routes):
        self.hits = []
        self.closed_by_client = threading.Event()
        hits, server = self.hits, self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                route = routes.get(self.path, (404, {}, b""))
                status, headers, body = route() if callable(route) else route
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if callable(body):
                    self.end_headers()
                    body(self, server)
                    return
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def serve():
    servers = []

    def make(routes):
        servers.append(_Server(routes))
        return servers[-1]
    yield make
    for s in servers:
        s.close()


class _Sink:
    """ffmpeg's stdin. gate: an Event each write waits on (a reader that
    has stopped reading)."""

    def __init__(self, gate=None, fail=False):
        self.data = bytearray()
        self.closed = threading.Event()
        self.gate = gate
        self.fail = fail

    def write(self, b):
        if self.fail:
            raise BrokenPipeError(32, "Broken pipe")
        if self.gate is not None:
            self.gate.wait(10)
        self.data += b

    def flush(self):
        pass

    def close(self):
        self.closed.set()


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


def _run(url, sink=None, timeout=10, **kw):
    sink = sink or _Sink()
    pump = live_fetch.StreamPump(url, sink, **kw).start()
    try:
        assert sink.closed.wait(timeout), "the pump never closed ffmpeg's stdin"
    finally:
        pump.halt()
        pump.join(5)
    assert not pump.alive()
    return pump, sink


def _segments(names, seq=0, end=True, extra=""):
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:1", f"#EXT-X-MEDIA-SEQUENCE:{seq}", extra]
    for name in names:
        lines += ["#EXTINF:1,", name]
    if end:
        lines.append("#EXT-X-ENDLIST")
    return ("\n".join(line for line in lines if line) + "\n").encode()


def _seg_routes(names):
    return {f"/{n}": (200, {}, n.encode() + b";") for n in names}


# --- parsing --------------------------------------------------------------

def test_parse_numbers_segments_from_the_media_sequence_and_resolves_uris():
    p = live_fetch.parse_playlist(
        "\ufeff\n#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:41\n"
        "#EXTINF:4,\na.ts\n#EXTINF:4,\nhttp://cdn.example/b.ts\n", "http://h.example/live/p.m3u8")
    assert [(s["seq"], s["uri"]) for s in p["segments"]] == [
        (41, "http://h.example/live/a.ts"), (42, "http://cdn.example/b.ts")]
    assert p["target"] == 4.0 and not p["endlist"] and not p["variants"]


@pytest.mark.parametrize("line", [
    "#EXT-X-KEY:METHOD=SAMPLE-AES,URI=\"k\"",
    "#EXT-X-KEY:METHOD=AES-128,URI=\"k\",KEYFORMAT=\"com.apple.streamingkeydelivery\"",
    "#EXT-X-KEY:METHOD=AES-128,URI=\"k\",IV=0x12",
    "#EXT-X-BYTERANGE:100@0",
    "#EXT-X-MAP:URI=\"i.mp4\",BYTERANGE=\"100@0\"",
])
def test_unsupported_hls_features_are_refused(line):
    with pytest.raises(live_fetch.StreamFetchError) as e:
        live_fetch.parse_playlist(f"#EXTM3U\n{line}\n#EXTINF:1,\na.ts\n", "http://h.example/")
    assert str(e.value) == live_fetch.UNSUPPORTED


def test_a_playlist_with_too_many_entries_is_refused():
    def playlist(n):
        return "#EXTM3U\n" + "".join(f"#EXTINF:1,\ns{i}.ts\n" for i in range(n))
    cap = live_fetch.MAX_PLAYLIST_ENTRIES
    assert len(live_fetch.parse_playlist(playlist(cap), "http://h/")["segments"]) == cap
    with pytest.raises(live_fetch.StreamFetchError) as exc:
        live_fetch.parse_playlist(playlist(cap + 1), "http://h/")
    assert str(exc.value) == live_fetch.TOO_LARGE and "http" not in str(exc.value)


def test_pick_rendition_takes_the_lowest_bandwidth_or_its_audio_rendition():
    master = live_fetch.parse_playlist(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000\nhi.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=64000\nlo.m3u8\n", "http://h.example/m.m3u8")
    assert live_fetch.pick_rendition(master) == "http://h.example/lo.m3u8"
    master = live_fetch.parse_playlist(
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="x",URI="en.m3u8"\n'
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="y",DEFAULT=YES,URI="zh.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=64000,AUDIO="a"\nlo.m3u8\n', "http://h.example/m.m3u8")
    assert live_fetch.pick_rendition(master) == "http://h.example/zh.m3u8"


# --- fetching -------------------------------------------------------------

def test_a_direct_stream_is_piped_as_it_arrives(serve):
    body = os.urandom(300_000)
    s = serve({"/a.flv": (200, {"Content-Type": "video/x-flv"}, body)})
    pump, sink = _run(f"{s.base}/a.flv")
    assert bytes(sink.data) == body and pump.error is None


def test_hls_master_variant_init_and_segments_in_order(serve):
    names = ["s0.m4s", "s1.m4s", "s2.m4s"]
    routes = {
        "/m.m3u8": (200, _M3U8, b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000\nhi.m3u8\n"
                                b"#EXT-X-STREAM-INF:BANDWIDTH=1000\nlo.m3u8\n"),
        "/lo.m3u8": (200, _M3U8, _segments(names, extra='#EXT-X-MAP:URI="init.mp4"')),
        "/init.mp4": (200, {}, b"INIT;"),
        **_seg_routes(names)}
    s = serve(routes)
    pump, sink = _run(f"{s.base}/m.m3u8")
    assert bytes(sink.data) == b"INIT;s0.m4s;s1.m4s;s2.m4s;"
    assert "/hi.m3u8" not in s.hits and pump.error is None


def test_a_live_playlist_starts_at_the_edge_and_tracks_new_segments_once(serve):
    """Each refresh slides the window by one; segments are emitted by
    sequence number, never twice, until the playlist ends."""
    state = {"n": 0}

    def playlist():
        state["n"] += 1
        first = state["n"] - 1
        names = [f"x{i}.ts" for i in range(first, first + 5)]
        return 200, _M3U8, _segments(names, seq=first, end=state["n"] >= 4)

    s = serve({"/live.m3u8": playlist, **_seg_routes([f"x{i}.ts" for i in range(10)])})
    pump, sink = _run(f"{s.base}/live.m3u8")
    # First load: the last LIVE_EDGE_SEGMENTS (x2..x4); then x5, x6, x7.
    assert bytes(sink.data) == b"x2.ts;x3.ts;x4.ts;x5.ts;x6.ts;x7.ts;"
    assert pump.error is None


def test_a_restarted_media_sequence_is_rejoined_at_the_edge(serve):
    state = {"n": 0}

    def playlist():
        state["n"] += 1
        if state["n"] == 1:
            return 200, _M3U8, _segments([f"a{i}.ts" for i in range(3)], seq=100, end=False)
        return 200, _M3U8, _segments([f"b{i}.ts" for i in range(3)], seq=0, end=True)

    s = serve({"/live.m3u8": playlist, **_seg_routes([f"a{i}.ts" for i in range(3)]),
               **_seg_routes([f"b{i}.ts" for i in range(3)])})
    _, sink = _run(f"{s.base}/live.m3u8")
    assert bytes(sink.data) == b"a0.ts;a1.ts;a2.ts;b0.ts;b1.ts;b2.ts;"


def _encrypt(data, key, iv):
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    padder = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(padder.update(data) + padder.finalize()) + enc.finalize()


def test_aes128_segments_are_decrypted_with_explicit_and_sequence_ivs(serve):
    pytest.importorskip("cryptography")
    key, iv = os.urandom(16), os.urandom(16)
    playlist = ("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:7\n"
                f'#EXT-X-KEY:METHOD=AES-128,URI="k.bin",IV=0x{iv.hex()}\n#EXTINF:1,\ne0.ts\n'
                '#EXT-X-KEY:METHOD=AES-128,URI="k.bin"\n#EXTINF:1,\ne1.ts\n'
                "#EXT-X-KEY:METHOD=NONE\n#EXTINF:1,\np2.ts\n#EXT-X-ENDLIST\n").encode()
    s = serve({"/p.m3u8": (200, _M3U8, playlist), "/k.bin": (200, {}, key),
               "/e0.ts": (200, {}, _encrypt(b"zero;", key, iv)),
               "/e1.ts": (200, {}, _encrypt(b"one;", key, (8).to_bytes(16, "big"))),
               "/p2.ts": (200, {}, b"two;")})
    pump, sink = _run(f"{s.base}/p.m3u8")
    assert bytes(sink.data) == b"zero;one;two;" and pump.error is None
    assert s.hits.count("/k.bin") == 1  # fetched once, then reused


def test_a_key_of_the_wrong_length_skips_its_segments(serve):
    pytest.importorskip("cryptography")
    playlist = ('#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n'
                "#EXTINF:1,\ne0.ts\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:1,\np1.ts\n"
                "#EXT-X-ENDLIST\n").encode()
    s = serve({"/p.m3u8": (200, _M3U8, playlist), "/k": (200, {}, b"short"),
               "/e0.ts": (200, {}, b"\0" * 32), "/p1.ts": (200, {}, b"one;")})
    _, sink = _run(f"{s.base}/p.m3u8")
    assert bytes(sink.data) == b"one;"


def test_a_failed_segment_is_skipped_and_the_rest_play(serve):
    s = serve({"/p.m3u8": (200, _M3U8, _segments(["a.ts", "gone.ts", "c.ts"])),
               **_seg_routes(["a.ts", "c.ts"])})
    pump, sink = _run(f"{s.base}/p.m3u8")
    assert bytes(sink.data) == b"a.ts;c.ts;" and pump.error is None


@pytest.mark.parametrize("uri", ["tcp://127.0.0.1:9/x", "httpproxy://127.0.0.1:9/x",
                                 "crypto+httpproxy://127.0.0.1:9/x", "file:///etc/passwd",
                                 "crypto:file:///etc/passwd", "data:,x"])
@pytest.mark.parametrize("position", ["variant", "segment", "key", "init", "redirect"])
def test_a_non_http_address_anywhere_ends_the_capture_unopened(serve, uri, position):
    playlists = {
        "variant": f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n{uri}\n",
        "segment": f"#EXTM3U\n#EXTINF:1,\n{uri}\n#EXT-X-ENDLIST\n",
        "key": f'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="{uri}"\n#EXTINF:1,\n/a.ts\n'
               "#EXT-X-ENDLIST\n",
        "init": f'#EXTM3U\n#EXT-X-MAP:URI="{uri}"\n#EXTINF:1,\n/a.ts\n#EXT-X-ENDLIST\n',
    }
    routes = {"/a.ts": (200, {}, b"\0" * 32)}
    if position == "redirect":
        routes["/p.m3u8"] = (302, {"Location": uri}, b"")
    else:
        routes["/p.m3u8"] = (200, _M3U8, playlists[position].encode())
    s = serve(routes)
    pump, sink = _run(f"{s.base}/p.m3u8")
    assert pump.error == live_fetch.NOT_HTTP
    assert bytes(sink.data) == b"" and "/a.ts" not in s.hits


def test_http_redirects_are_followed_up_to_the_limit(serve, monkeypatch):
    monkeypatch.setattr(live_fetch, "MAX_REDIRECTS", 2)
    s = serve({"/r1": (302, {"Location": "/r2"}, b""), "/r2": (301, {"Location": "/a"}, b""),
               "/a": (200, {}, b"ok"),
               "/l1": (302, {"Location": "/l2"}, b""), "/l2": (302, {"Location": "/l3"}, b""),
               "/l3": (302, {"Location": "/a"}, b"")})
    pump, sink = _run(f"{s.base}/r1")
    assert bytes(sink.data) == b"ok" and pump.error is None
    pump, sink = _run(f"{s.base}/l1")
    assert pump.error == live_fetch.UNREACHABLE and bytes(sink.data) == b""


def test_errors_are_fixed_text_without_the_url(serve):
    s = serve({})
    pump, _ = _run(f"{s.base}/missing?token=abc")
    assert pump.error == "The stream server answered HTTP 404."
    pump, _ = _run("http://127.0.0.1:1/x?token=abc")  # nothing listens on port 1
    assert pump.error == live_fetch.UNREACHABLE


# --- latency, backpressure, stalls, stop ------------------------------------

def test_a_live_playlist_that_stops_growing_ends_as_stalled(serve):
    s = serve({"/live.m3u8": (200, _M3U8, _segments(["a.ts"], end=False)),
               **_seg_routes(["a.ts"])})
    started = time.monotonic()
    pump, sink = _run(f"{s.base}/live.m3u8", stall_timeout=1.5)
    assert pump.error == live_fetch.STALLED
    assert bytes(sink.data) == b"a.ts;"
    assert time.monotonic() - started < 6
    assert s.hits.count("/live.m3u8") >= 2  # it kept polling until then


def test_a_body_that_stops_arriving_ends_as_stalled(serve, monkeypatch):
    monkeypatch.setattr(live_fetch, "READ_TIMEOUT", 0.5)

    def trickle(handler, server):
        handler.wfile.write(b"first")
        handler.wfile.flush()
        time.sleep(3)

    s = serve({"/a": (200, {}, trickle)})
    pump, sink = _run(f"{s.base}/a")
    assert pump.error == live_fetch.STALLED and bytes(sink.data) == b"first"


def test_backpressure_bounds_the_queue_then_a_stuck_ffmpeg_ends_the_capture(serve, monkeypatch):
    monkeypatch.setattr(live_fetch, "QUEUE_CHUNKS", 2)
    monkeypatch.setattr(live_fetch, "CHUNK_BYTES", 1024)
    gate = threading.Event()
    sink = _Sink(gate=gate)
    s = serve({"/a": (200, {}, os.urandom(200 * 1024))})
    pump = live_fetch.StreamPump(f"{s.base}/a", sink, stall_timeout=1.0).start()
    try:
        deadline = time.monotonic() + 5
        while pump.error is None and time.monotonic() < deadline:
            assert pump._queue.qsize() <= 2
            time.sleep(0.02)
        assert pump.error == live_fetch.FFMPEG_STALLED
    finally:
        gate.set()
        pump.halt()
        pump.join(5)
    assert not pump.alive() and sink.closed.is_set()
    assert len(sink.data) < 200 * 1024  # it stopped fetching, not buffered it all


def test_a_broken_pipe_ends_the_capture_and_the_fetch(serve):
    def endless(handler, server):
        try:
            while True:
                handler.wfile.write(b"\0" * 4096)
                time.sleep(0.01)
        except OSError:
            server.closed_by_client.set()

    s = serve({"/a": (200, {}, endless)})
    pump, _ = _run(f"{s.base}/a", sink=_Sink(fail=True))
    assert pump.error == live_fetch.FFMPEG_STALLED
    assert s.closed_by_client.wait(5)


def test_stop_ends_a_blocked_fetch_promptly_and_closes_its_connection(serve):
    """halt() wakes a fetch blocked reading (well inside READ_TIMEOUT), the
    threads end, ffmpeg's stdin is closed and the server sees the
    connection go: nothing is left open."""
    def hang(handler, server):
        handler.wfile.write(b"some")
        handler.wfile.flush()
        handler.connection.settimeout(10)
        if _until_peer_closes(handler.connection):
            server.closed_by_client.set()

    s = serve({"/a": (200, {}, hang)})
    sink = _Sink()
    pump = live_fetch.StreamPump(f"{s.base}/a", sink).start()
    deadline = time.monotonic() + 5
    while bytes(sink.data) != b"some" and time.monotonic() < deadline:
        time.sleep(0.02)
    started = time.monotonic()
    pump.halt()
    pump.join(5)
    assert time.monotonic() - started < 3
    assert not pump.alive() and sink.closed.is_set()
    assert pump.error is None  # a stop is not a failure
    assert s.closed_by_client.wait(5)
    assert not [t for t in threading.enumerate() if t.name in ("live-fetch", "live-pipe")]


class _Silent:
    """Accepts connections and reads the request, never answers; records
    when the client closes its side."""

    def __init__(self):
        self.accepted = threading.Event()
        self.closed_by_client = threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.base = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        self.accepted.set()
        conn.settimeout(10)
        if _until_peer_closes(conn):
            self.closed_by_client.set()
        conn.close()

    def close(self):
        self.sock.close()


def test_stop_while_waiting_for_response_headers_is_prompt():
    """No response exists yet, so only the connection's own socket can
    wake the fetch: halt() shuts it down, nothing is left open."""
    server = _Silent()
    sink = _Sink()
    pump = live_fetch.StreamPump(f"{server.base}/a", sink).start()
    try:
        assert server.accepted.wait(5)
        time.sleep(0.2)  # the request is sent; the fetch waits for headers
        started = time.monotonic()
        pump.halt()
        pump.join(5)
        assert time.monotonic() - started < 3
        assert not pump.alive() and sink.closed.is_set() and pump.error is None
        assert server.closed_by_client.wait(5)
        assert not [t for t in threading.enumerate() if t.name in ("live-fetch", "live-pipe")]
    finally:
        pump.halt()
        server.close()


def test_a_segment_that_trickles_past_the_stall_timeout_ends_the_capture(serve):
    """Each byte arrives well inside READ_TIMEOUT; the request as a whole
    is still bounded."""
    def trickle(handler, server):
        try:
            for _ in range(200):
                handler.wfile.write(b"x")
                handler.wfile.flush()
                time.sleep(0.05)
        except OSError:
            pass

    s = serve({"/p.m3u8": (200, _M3U8, _segments(["slow.ts"])),
               "/slow.ts": (200, {}, trickle)})
    started = time.monotonic()
    pump, sink = _run(f"{s.base}/p.m3u8", stall_timeout=0.5)
    assert pump.error == live_fetch.STALLED and bytes(sink.data) == b""
    assert time.monotonic() - started < 5


def test_stop_during_the_playlist_wait_is_prompt(serve):
    s = serve({"/live.m3u8": (200, _M3U8, b"#EXTM3U\n#EXT-X-TARGETDURATION:30\n"
                                          b"#EXTINF:30,\na.ts\n"),
               **_seg_routes(["a.ts"])})
    sink = _Sink()
    pump = live_fetch.StreamPump(f"{s.base}/live.m3u8", sink).start()
    deadline = time.monotonic() + 5
    while not sink.data and time.monotonic() < deadline:
        time.sleep(0.02)
    started = time.monotonic()
    pump.halt()
    pump.join(5)
    assert time.monotonic() - started < 2 and not pump.alive()


def test_the_session_ignores_environment_proxies(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://corp.example:3128")
    monkeypatch.setenv("NO_PROXY", "*")
    pump = live_fetch.StreamPump("http://x.example/", _Sink(), proxy="http://u:p@127.0.0.1:9")
    assert pump._session.trust_env is False
    assert pump._session.proxies == {"http": "http://u:p@127.0.0.1:9",
                                     "https": "http://u:p@127.0.0.1:9"}
    assert live_fetch.StreamPump("http://x.example/", _Sink())._session.proxies == {}
