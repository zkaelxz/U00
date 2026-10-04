"""services/capped_body.read_capped: cap, declared length, deadline, close."""
import pytest

from services import capped_body


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
