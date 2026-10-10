"""Security review MED-1: sources.http's real transport streams the body
under a size cap (pages and images separately), an overall per-request
deadline and cancel between chunks, and always closes the response; the
pasted-URL preview job ends with a fixed-text 422 (too large) / 503 (too
slow) or as cancelled. The requests session and DNS are faked: no network."""
import time

import pytest

import background_jobs
from lib import url_guard
from sources import http, registry
from sources.http import (Cancelled, FetchLimits, PacingPolicy, ResponseTooLarge,
                          ResponseTooSlow, SourceClient)

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
PAGE = f"https://novel.example/book/1/ch2.html?token={SECRET}"


class FakeRaw:
    def __init__(self, chunks, on_read=None):
        self.chunks, self.on_read, self.reads = list(chunks), on_read, 0

    def read1(self, n, decode_content=True):
        self.reads += 1
        if self.on_read:
            self.on_read(self)
        if not self.chunks:
            return b""
        c = self.chunks.pop(0)
        if len(c) > n:   # like read1: at most n, the rest stays buffered
            self.chunks.insert(0, c[n:])
            c = c[:n]
        return c


class FakeResp:
    def __init__(self, chunks=(), headers=None, status=200, url=PAGE, on_read=None):
        self.status_code, self.url = status, url
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        self.raw = FakeRaw(chunks, on_read)
        self.cookies, self.history, self.closed = [], [], False

    def close(self):
        self.closed = True

    @property
    def content(self):   # the old, unbounded path must never be used
        raise AssertionError("r.content read")


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kw):
        http._guard(url)  # the real session guards every hop
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


@pytest.fixture
def net(isolated_db, monkeypatch):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    holder = {}

    def use(*responses):
        holder["s"] = FakeSession(responses)
        return holder["s"]
    monkeypatch.setattr(http, "_thread_session", lambda: holder["s"])
    http.reset_pacing_state()
    yield use
    http.reset_pacing_state()


def _limits(**kw):
    return FetchLimits(**{"max_page_bytes": 1000, "max_image_bytes": 5000, "deadline": 60, **kw})


def test_streams_and_reads_whole_small_body(net):
    s = net(FakeResp([b"<html>", b"hello</html>"]))
    r = http._requests_transport("GET", PAGE, {}, None, 5, limits=_limits())
    assert r.content == b"<html>hello</html>" and r.status_code == 200
    assert s.calls[0][2]["stream"] is True and s.calls[0][2]["allow_redirects"] is False


def test_content_length_over_cap_refused_before_reading(net):
    resp = FakeResp([b"x" * 10], headers={"Content-Length": "1001"})
    net(resp)
    with pytest.raises(ResponseTooLarge) as e:
        http._requests_transport("GET", PAGE, {}, None, 5, limits=_limits())
    assert resp.raw.reads == 0 and resp.closed
    assert str(e.value) == http.TOO_LARGE and "novel.example" not in str(e.value)


def test_streamed_body_over_cap_stops_reading(net):
    resp = FakeResp([b"x" * 600] * 50)   # no Content-Length
    net(resp)
    with pytest.raises(ResponseTooLarge):
        http._requests_transport("GET", PAGE, {}, None, 5, limits=_limits())
    assert resp.raw.reads == 2 and resp.closed   # never the whole body


def test_images_get_their_own_cap(net):
    net(FakeResp([b"i" * 900] * 4, headers={"Content-Type": "image/png"}))
    r = http._requests_transport("GET", PAGE, {}, None, 5, limits=_limits())
    assert len(r.content) == 3600
    net(FakeResp([b"i" * 900] * 6, headers={"Content-Type": "image/png"}))
    with pytest.raises(ResponseTooLarge):
        http._requests_transport("GET", PAGE, {}, None, 5, limits=_limits())


def test_slow_drip_hits_the_deadline(net):
    now = [0.0]
    resp = FakeResp([b"."] * 10_000, on_read=lambda raw: now.__setitem__(0, now[0] + 7.0))
    net(resp)
    with pytest.raises(ResponseTooSlow) as e:
        http._requests_transport("GET", PAGE, {}, None, 5,
                                 limits=_limits(deadline=60, clock=lambda: now[0]))
    assert resp.raw.reads <= 10 and resp.closed
    assert str(e.value) == http.TOO_SLOW


def test_cancel_between_chunks(net):
    flag = {"cancel": False}
    resp = FakeResp([b"." ] * 100, on_read=lambda raw: flag.__setitem__("cancel", raw.reads >= 3))
    net(resp)
    with pytest.raises(Cancelled):
        http._requests_transport("GET", PAGE, {}, None, 5,
                                 limits=_limits(cancel_check=lambda: flag["cancel"]))
    assert resp.raw.reads == 3 and resp.closed


def test_redirect_hops_are_closed_and_revalidated(net, monkeypatch):
    hop = FakeResp(status=302, headers={"Location": "https://other.example/x"})
    final = FakeResp([b"ok"])
    s = net(hop, final)
    seen = []
    real = url_guard.resolve_public
    monkeypatch.setattr(url_guard, "resolve_public", lambda u: (seen.append(u), real(u))[1])
    r = http._requests_transport("GET", PAGE, {}, None, 5, limits=_limits())
    assert r.content == b"ok" and hop.closed and final.closed
    assert len(s.calls) == 2 and [u.split("/")[2] for u in seen] == ["novel.example",
                                                                      "other.example"]


def test_client_does_not_retry_a_refused_response_and_uses_its_limits(net):
    s = net(FakeResp([b"x" * 600] * 5))
    c = SourceClient("generic", policy=PacingPolicy(min_delay=0, max_delay=0,
                                                    session_break_min_requests=0),
                     max_page_bytes=1000)
    with pytest.raises(ResponseTooLarge):
        c.get(PAGE, use_cache=False)
    assert len(s.calls) == 1


# ---------------------------------------------------------------------------
# Security review M-1: the body is read raw and decoded by BodyDecoder, so a
# deflate stream of endless empty stored blocks (urllib3's own read1 loops
# on it forever) still reaches the deadline/cancel/cap checks
# ---------------------------------------------------------------------------

import gzip  # noqa: E402
import io  # noqa: E402
import zlib  # noqa: E402

EMPTY_BLOCK = b"\x00\x00\x00\xff\xff"   # raw deflate: non-final stored block, 0 bytes


class _EndlessEmptyBlocks(io.RawIOBase):
    def __init__(self):
        self.pos = 0

    def readable(self):
        return True

    def readinto(self, b):
        n = min(len(b), 8192)
        k = self.pos % len(EMPTY_BLOCK)
        b[:n] = (EMPTY_BLOCK * (n // len(EMPTY_BLOCK) + 2))[k:k + n]
        self.pos += n
        return n


class RealRaw:
    """A real urllib3 HTTPResponse over a stream, with FakeRaw's read counter
    and hook; forwards decode_content so a regression to True would hang
    (the test would then time out rather than pass)."""

    def __init__(self, stream, encoding, on_read=None):
        import urllib3
        self.resp = urllib3.HTTPResponse(body=io.BufferedReader(stream),
                                         headers={"Content-Encoding": encoding},
                                         preload_content=False, decode_content=False)
        self.on_read, self.reads = on_read, 0

    def read1(self, n, decode_content=None):
        assert decode_content is False
        self.reads += 1
        if self.on_read:
            self.on_read(self)
        return self.resp.read1(n, decode_content=decode_content)


def _endless_resp(on_read=None, encoding="deflate"):
    resp = FakeResp(headers={"Content-Encoding": encoding})
    resp.raw = RealRaw(_EndlessEmptyBlocks(), encoding, on_read)
    return resp


def test_endless_empty_deflate_blocks_hit_the_raw_cap(net):
    resp = _endless_resp()
    net(resp)
    with pytest.raises(ResponseTooLarge):
        http._requests_transport("GET", PAGE, {}, None, 5,
                                 limits=_limits(max_page_bytes=200_000))
    assert resp.closed and resp.raw.reads <= 200_000 // 8192 + 2   # read1 returns 8 KB here


def test_endless_empty_deflate_blocks_hit_the_deadline(net):
    now = [0.0]
    resp = _endless_resp(on_read=lambda raw: now.__setitem__(0, now[0] + 7.0))
    net(resp)
    with pytest.raises(ResponseTooSlow):
        http._requests_transport("GET", PAGE, {}, None, 5,
                                 limits=_limits(max_page_bytes=10**9, deadline=60,
                                                clock=lambda: now[0]))
    assert resp.closed and resp.raw.reads <= 10


def test_endless_empty_deflate_blocks_cancel(net):
    flag = {"cancel": False}
    resp = _endless_resp(on_read=lambda raw: flag.__setitem__("cancel", raw.reads >= 3))
    net(resp)
    with pytest.raises(Cancelled):
        http._requests_transport("GET", PAGE, {}, None, 5,
                                 limits=_limits(max_page_bytes=10**9,
                                                cancel_check=lambda: flag["cancel"]))
    assert resp.raw.reads == 3 and resp.closed


def test_compressed_body_decoded_bytes_are_capped(net):
    bomb = gzip.compress(b"\0" * 5_000_000)   # ~5 KB on the wire
    assert len(bomb) < 1000 * 10
    resp = FakeResp([bomb], headers={"Content-Encoding": "gzip"})
    net(resp)
    with pytest.raises(ResponseTooLarge):
        http._requests_transport("GET", PAGE, {}, None, 5,
                                 limits=_limits(max_page_bytes=50_000))
    assert resp.closed


@pytest.mark.parametrize("encoding,body", [
    ("gzip", gzip.compress(b"<html>hi</html>", mtime=0)),   # fixed mtime: ids must match across xdist workers
    ("deflate", zlib.compress(b"<html>hi</html>")),
    ("deflate", zlib.compress(b"<html>hi</html>")[2:-4]),   # raw deflate, as some servers send
    ("identity", b"<html>hi</html>"),
])
def test_gzip_and_deflate_are_decoded(net, encoding, body):
    net(FakeResp([body[:3], body[3:]], headers={"Content-Encoding": encoding}))
    r = http._requests_transport("GET", PAGE, {}, None, 5, limits=_limits())
    assert r.content == b"<html>hi</html>"


def test_other_encodings_refused_and_only_gzip_deflate_offered(net):
    resp = FakeResp([b"xx"], headers={"Content-Encoding": "br"})
    s = net(resp)
    with pytest.raises(http.UnsupportedEncoding) as e:
        http._requests_transport("GET", PAGE, {}, None, 5, limits=_limits())
    assert str(e.value) == http.UNSUPPORTED_ENCODING and resp.raw.reads == 0 and resp.closed
    assert s.calls[0][2]["headers"]["Accept-Encoding"] == "gzip, deflate"


# ---------------------------------------------------------------------------
# The pasted-URL preview job (R1), end to end through the real ladder
# ---------------------------------------------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


@pytest.fixture
def api(net, monkeypatch):
    from fastapi.testclient import TestClient

    from api.api_config import ApiSettings
    from api.server import create_app
    from sources import store
    monkeypatch.setattr(registry, "adapter_classes", lambda: {})
    for k, v in (("pace_min_delay", 0), ("pace_max_delay", 0), ("session_break_min_requests", 0),
                 ("max_retries", 0)):
        store.set_setting(k, v)
    yield TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    _wait("sources_url_preview")
    background_jobs.clear_job("sources_url_preview")


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if not st or st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _preview(api):
    r = api.post("/api/sources/url/preview", json={"url": PAGE})
    assert r.status_code == 200, r.text
    return _wait("sources_url_preview")


def test_preview_over_cap_is_a_fixed_422(api, net):
    net(FakeResp([b"x" * 65536] * 200))   # ~13 MB, over the pasted-URL 5 MB cap
    st = _preview(api)
    assert st["status"] == "error"
    r = api.get("/api/sources/jobs/sources_url_preview/result")
    assert r.status_code == 422 and r.json()["error"]["message"] == http.TOO_LARGE
    assert SECRET not in r.text and "novel.example" not in r.text


def test_preview_slow_drip_is_a_fixed_503(api, net, monkeypatch):
    from services import sources_url_service
    monkeypatch.setattr(sources_url_service, "PASTED_REQUEST_DEADLINE", 0.3)
    net(FakeResp([b"."] * 10_000, on_read=lambda raw: time.sleep(0.01)))
    st = _preview(api)
    assert st["status"] == "error"
    r = api.get("/api/sources/jobs/sources_url_preview/result")
    assert r.status_code == 503 and r.json()["error"]["message"] == http.TOO_SLOW
    # the fixed-id job is free again
    assert background_jobs.get_status("sources_url_preview")["status"] == "error"


def test_preview_cancel_mid_read_ends_the_job(api, net):
    def on_read(raw):
        if raw.reads == 3:
            background_jobs.request_cancel("sources_url_preview")
        time.sleep(0.005)
    resp = FakeResp([b"."] * 100_000, on_read=on_read)
    net(resp)
    st = _preview(api)
    assert st["status"] == "cancelled"
    assert resp.closed and resp.raw.reads < 10


def test_preview_endless_empty_deflate_blocks_cancel_ends_the_job(api, net):
    def on_read(raw):
        if raw.reads == 3:
            background_jobs.request_cancel("sources_url_preview")
        time.sleep(0.005)
    resp = _endless_resp(on_read=on_read)
    net(resp)
    st = _preview(api)
    assert st["status"] == "cancelled"
    assert resp.closed and resp.raw.reads < 10


def test_thread_session_is_the_guarded_front_door_session():
    s = http._thread_session()
    assert s.guard is http._guard and s.timeout and s.max_bytes >= http.MAX_IMAGE_BYTES
