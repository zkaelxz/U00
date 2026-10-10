"""lib/capped_body.read_capped: cap, declared length, deadline, close."""
import pytest

from lib import capped_body


class Boom(Exception):
    pass


class Resp:
    def __init__(self, chunks, headers=None):
        self.chunks = chunks
        self.headers = headers or {}
        self.closed = False
        self.reads = 0

    def iter_content(self, size):
        for c in self.chunks:
            self.reads += 1
            yield c

    def close(self):
        self.closed = True


def _read(resp, cap=100, deadline=10.0, clock=None):
    return capped_body.read_capped(resp, cap, deadline, Boom, chunk_size=4, clock=clock)


def test_returns_body_and_closes():
    r = Resp([b"ab", b"cd"])
    assert _read(r) == b"abcd"
    assert r.closed


def test_cap_exceeded_raises_and_closes():
    r = Resp([b"x" * 60, b"x" * 60])
    with pytest.raises(Boom):
        _read(r)
    assert r.closed


def test_exactly_at_cap_is_allowed():
    assert _read(Resp([b"x" * 100])) == b"x" * 100


def test_declared_length_over_cap_refused_before_reading():
    r = Resp([b"x"], headers={"Content-Length": "101"})
    with pytest.raises(Boom):
        _read(r)
    assert r.reads == 0 and r.closed


def test_malformed_declared_length_is_ignored():
    assert _read(Resp([b"ok"], headers={"Content-Length": "abc"})) == b"ok"


@pytest.mark.parametrize("raw", ["-5", " ", "5, 5", "\u00b2", "\u00b3", "1e3", "0x10", "\u0663"])
def test_odd_content_length_values_are_ignored_not_raised(raw):
    resp = Resp([b"ok"], headers={"Content-Length": raw})
    assert capped_body.declared_length(resp) is None
    assert _read(resp) == b"ok"


def test_content_length_with_spaces_is_read():
    assert capped_body.declared_length(Resp([], headers={"Content-Length": " 12 "})) == 12


def test_huge_content_length_is_refused_without_reading():
    resp = Resp([b"x"], headers={"Content-Length": "9" * 30})
    with pytest.raises(Boom):
        _read(resp)
    assert resp.reads == 0 and resp.closed


def test_deadline_can_raise_its_own_error():
    class TooSlow(Exception):
        pass
    ticks = iter([0.0, 1.0, 11.0])
    r = Resp([b"a", b"b", b"c"])
    with pytest.raises(TooSlow):
        capped_body.read_capped(r, 100, 10.0, Boom, chunk_size=4,
                                clock=lambda: next(ticks), make_deadline_error=TooSlow)
    assert r.closed


def test_cap_still_uses_the_main_error_when_a_deadline_error_is_given():
    class TooSlow(Exception):
        pass
    r = Resp([b"x" * 200])
    with pytest.raises(Boom):
        capped_body.read_capped(r, 100, 10.0, Boom, chunk_size=4, make_deadline_error=TooSlow)


def test_deadline_exceeded_with_fake_clock():
    ticks = iter([0.0, 1.0, 11.0])
    r = Resp([b"a", b"b", b"c"])
    with pytest.raises(Boom):
        _read(r, clock=lambda: next(ticks))
    assert r.closed and r.reads == 2


def test_closes_when_iteration_itself_fails():
    class Bad(Resp):
        def iter_content(self, size):
            raise OSError("reset")
            yield b""
    r = Bad([])
    with pytest.raises(OSError):
        _read(r)
    assert r.closed


def test_error_text_is_the_callers_only():
    r = Resp([b"x" * 200], headers={"Authorization": "Bearer secret"})
    with pytest.raises(Boom) as ei:
        _read(r)
    assert str(ei.value) == ""


def test_reads_an_httpx_response():
    httpx = pytest.importorskip("httpx")
    resp = httpx.Response(200, stream=httpx.ByteStream(b"x" * 10))
    with pytest.raises(Boom):
        capped_body.read_capped(resp, 5, 10.0, Boom)
    assert resp.is_closed
    assert capped_body.read_capped(httpx.Response(200, stream=httpx.ByteStream(b"ok")),
                                   5, 10.0, Boom) == b"ok"
