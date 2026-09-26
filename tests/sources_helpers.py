"""
tests/sources_helpers.py -- shared fakes for the source-adapter tests:
a controllable clock, a scripted HTTP transport, and PNG fixtures.
Nothing here touches the network.
"""

import io

from sources.http import Response


class FakeClock:
    """clock()/sleep() pair: sleeping advances time instantly and is
    recorded, so pacing and backoff can be asserted exactly."""

    def __init__(self, start: float = 1000.0):
        self.t = start
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    @property
    def slept(self) -> float:
        return sum(self.sleeps)


class FixedRng:
    """Stands in for random.Random: uniform(a, b) always returns `frac`
    of the way from a to b."""

    def __init__(self, frac: float = 0.0):
        self.frac = frac

    def uniform(self, a, b):
        return a + (b - a) * self.frac


class ScriptedTransport:
    """Maps URL -> a Response, a list of Responses (served in order, last
    one repeating), an Exception to raise, or a callable(url) -> Response.
    Records every call with the fake clock's time."""

    def __init__(self, routes: dict = None, clock: FakeClock = None):
        self.routes = dict(routes or {})
        self.calls = []
        self.clock = clock

    def __call__(self, method, url, headers, data, timeout):
        assert timeout, "every request must carry a timeout"
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "data": data,
                           "t": self.clock.clock() if self.clock else None})
        route = self.routes.get(url)
        if route is None:
            return Response(404, {"content-type": "text/html"}, b"<html>not found</html>", url)
        if callable(route) and not isinstance(route, Response):
            return route(url)
        if isinstance(route, list):
            item = route.pop(0) if len(route) > 1 else route[0]
        else:
            item = route
        if isinstance(item, Exception):
            raise item
        return Response(item.status_code, dict(item.headers), item.content, item.url or url,
                        cookies=dict(getattr(item, "cookies", {}) or {}))

    def urls(self):
        return [c["url"] for c in self.calls]


def html(body: str, status: int = 200, headers: dict = None, url: str = "") -> Response:
    h = {"content-type": "text/html; charset=utf-8"}
    h.update(headers or {})
    return Response(status, h, body.encode("utf-8"), url)


def png(w: int, h: int, seed: int = 0) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), (seed * 37 % 256, seed * 91 % 256, seed * 53 % 256))
    ImageDraw.Draw(img).text((5, 5), f"img {seed}", fill="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def image(w: int, h: int, seed: int = 0) -> Response:
    return Response(200, {"content-type": "image/png"}, png(w, h, seed), "")


def make_client(source="test", transport=None, clock=None, rng=None, **policy_kw):
    from sources.http import PacingPolicy, SourceClient, reset_pacing_state
    reset_pacing_state()
    clock = clock or FakeClock()
    policy = PacingPolicy(**{"min_delay": 0.0, "max_delay": 0.0, **policy_kw})
    return SourceClient(source, policy=policy, transport=transport, sleep=clock.sleep,
                        clock=clock.clock, rng=rng or FixedRng(0.0))
