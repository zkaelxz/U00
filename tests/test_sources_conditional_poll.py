"""Step 106: chapter-list re-polls are conditional. A poll that was one
plain GET saves its ETag / Last-Modified; the next poll sends them back,
and a 304 skips the download and the parse. Multi-request, browser or
redirected chapter lists never use validators. The server is faked (a
scripted transport, and once the real transport over a fake requests
session): no network."""
import json

import pytest

from services import url_guard
from sources import chapter_check, http, store
from sources.base import SourceAdapter
from sources.http import Response
from sources.models import ChapterInfo

from .sources_helpers import make_client

LIST = "https://novel.example/book/1/chapters.json"
PAGE2 = "https://novel.example/book/1/chapters.json?page=2"
ETAG = '"v1-abc"'
LAST_MOD = "Tue, 29 Sep 2026 10:00:00 GMT"


class FakeServer:
    """Serves the chapter list with an ETag and Last-Modified and honours
    If-None-Match / If-Modified-Since like a real server would."""

    def __init__(self, chapters=("c1", "c2"), etag=ETAG, last_modified=LAST_MOD):
        self.chapters, self.etag, self.last_modified = list(chapters), etag, last_modified
        self.calls = []

    def __call__(self, method, url, headers, data, timeout):
        assert timeout
        self.calls.append({"method": method, "url": url, "headers": dict(headers)})
        inm = headers.get("If-None-Match")
        ims = headers.get("If-Modified-Since")
        hdrs = {}
        if self.etag:
            hdrs["ETag"] = self.etag
        if self.last_modified:
            hdrs["Last-Modified"] = self.last_modified
        if (inm and inm == self.etag) or (not inm and ims and ims == self.last_modified):
            return Response(304, hdrs, b"", url)
        body = json.dumps(self.chapters).encode()
        return Response(200, {"content-type": "application/json", **hdrs}, body, url)

    def change(self, chapters, etag='"v2-def"'):
        self.chapters, self.etag = list(chapters), etag


class ListAdapter(SourceAdapter):
    name = "condtest"
    display_name = "Cond Test"

    def __init__(self, client, pages=(LIST,)):
        super().__init__(client=client)
        self.pages, self.parses = list(pages), 0

    def get_chapters(self, series_id):
        out = []
        for url in self.pages:
            resp = self.client.get(url)
            self.parses += 1
            out += [ChapterInfo(source=self.name, series_id=series_id, chapter_id=c,
                                title=c, url=f"{url}#{c}")
                    for c in json.loads(resp.content)]
        return out


@pytest.fixture
def tracked(isolated_db):
    store.track_series("condtest", "s1", "Series", "", None)
    http.reset_pacing_state()
    yield store.list_tracked_series()[0]
    http.reset_pacing_state()


def _check(adapter, row):
    return [c.chapter_id for c in chapter_check.check_series(adapter, row)]


def test_unchanged_repoll_gets_304_and_skips_parse(tracked):
    server = FakeServer()
    adapter = ListAdapter(make_client("condtest", server))
    assert _check(adapter, tracked) == ["c1", "c2"]
    assert "If-None-Match" not in server.calls[0]["headers"]
    assert store.poll_validators("condtest", "s1") == {
        "url": LIST, "etag": ETAG, "last_modified": LAST_MOD}

    assert _check(adapter, tracked) == []
    sent = server.calls[1]["headers"]
    assert sent["If-None-Match"] == ETAG and sent["If-Modified-Since"] == LAST_MOD
    assert adapter.parses == 1          # the 304 poll never parsed anything
    assert store.list_tracked_series()[0]["last_checked"] is not None
    assert store.list_tracked_series()[0]["last_check_error"] is None
    assert adapter.client.attempts[-1].ok and adapter.client.attempts[-1].http_status == 304


def test_changed_list_is_fetched_parsed_and_validators_move_on(tracked):
    server = FakeServer()
    adapter = ListAdapter(make_client("condtest", server))
    _check(adapter, tracked)
    server.change(["c1", "c2", "c3"])
    assert _check(adapter, tracked) == ["c3"]
    assert adapter.parses == 2
    assert store.poll_validators("condtest", "s1")["etag"] == '"v2-def"'
    assert _check(adapter, tracked) == [] and adapter.parses == 2


def test_last_modified_alone_is_used(tracked):
    server = FakeServer(etag="")
    adapter = ListAdapter(make_client("condtest", server))
    _check(adapter, tracked)
    assert _check(adapter, tracked) == []
    assert "If-None-Match" not in server.calls[1]["headers"]
    assert server.calls[1]["headers"]["If-Modified-Since"] == LAST_MOD


def test_no_validators_means_plain_polls(tracked):
    server = FakeServer(etag="", last_modified="")
    adapter = ListAdapter(make_client("condtest", server))
    _check(adapter, tracked)
    _check(adapter, tracked)
    assert store.poll_validators("condtest", "s1") == {}
    assert adapter.parses == 2
    assert not any(k.startswith("If-") for c in server.calls for k in c["headers"])


def test_multi_request_chapter_list_never_uses_validators(tracked):
    server = FakeServer()
    adapter = ListAdapter(make_client("condtest", server), pages=(LIST, PAGE2))
    _check(adapter, tracked)
    _check(adapter, tracked)
    assert store.poll_validators("condtest", "s1") == {}
    assert not any(k.startswith("If-") for c in server.calls for k in c["headers"])
    assert adapter.parses == 4


def test_browser_rendered_poll_saves_no_validators(tracked):
    server = FakeServer()

    class Rendered(ListAdapter):
        def get_chapters(self, series_id):
            self.client.paced(lambda u: None, LIST, "Browser session")
            return super().get_chapters(series_id)

    _check(Rendered(make_client("condtest", server)), tracked)
    assert store.poll_validators("condtest", "s1") == {}


def test_redirected_list_saves_no_validators(tracked):
    def moved(method, url, headers, data, timeout):
        return Response(200, {"ETag": ETAG, "content-type": "application/json"},
                        b'["c1"]', "https://novel.example/elsewhere.json")
    _check(ListAdapter(make_client("condtest", moved)), tracked)
    assert store.poll_validators("condtest", "s1") == {}


def test_validators_only_sent_inside_a_poll(tracked):
    """An import (or any fetch outside a chapter check) is never conditional."""
    server = FakeServer()
    adapter = ListAdapter(make_client("condtest", server))
    _check(adapter, tracked)
    adapter.get_chapters("s1")
    assert not any(k.startswith("If-") for k in server.calls[-1]["headers"])


def test_unsafe_validators_are_dropped():
    assert http.clean_validator('"ok"') == '"ok"'
    assert http.clean_validator('"a"\r\nX-Evil: 1') == ""
    assert http.clean_validator("x" * 300) == ""
    assert http.clean_validator(None) == ""


def test_untrack_forgets_validators(tracked):
    _check(ListAdapter(make_client("condtest", FakeServer())), tracked)
    store.untrack_series("condtest", "s1")
    assert store.poll_validators("condtest", "s1") == {}


def test_adapter_swallowing_not_modified_still_counts_as_unchanged(tracked):
    server = FakeServer()

    class Swallows(ListAdapter):
        def get_chapters(self, series_id):
            try:
                return super().get_chapters(series_id)
            except Exception:
                return []

    adapter = Swallows(make_client("condtest", server))
    assert _check(adapter, tracked) == ["c1", "c2"]
    server.change(["c1", "c2", "c3"], etag=ETAG)   # same ETag: server says unchanged
    assert _check(adapter, tracked) == []
    assert store.poll_validators("condtest", "s1")["etag"] == ETAG


# -- the real transport: guards still apply, and a 304 passes through -----

class _Raw:
    def __init__(self, body):
        self.body = body

    def read1(self, n, decode_content=True):
        out, self.body = self.body[:n], self.body[n:]
        return out


class _Resp:
    def __init__(self, status, headers, body, url):
        self.status_code, self.headers, self.url = status, headers, url
        self.raw, self.cookies, self.history = _Raw(body), [], []

    def close(self):
        pass


class _Session:
    def __init__(self, server):
        self.server, self.calls = server, []

    def request(self, method, url, headers=None, **kw):
        assert kw["timeout"] and kw["allow_redirects"] is False and kw["stream"] is True
        self.calls.append(dict(headers))
        r = self.server(method, url, headers, None, kw["timeout"])
        return _Resp(r.status_code, dict(r.headers), r.content, url)


def test_real_transport_fake_server_304(tracked, monkeypatch):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    session = _Session(FakeServer())
    monkeypatch.setattr(http, "_thread_session", lambda: session)
    client = make_client("condtest")
    client.transport = client._limited_transport
    adapter = ListAdapter(client)
    assert _check(adapter, tracked) == ["c1", "c2"]
    assert _check(adapter, tracked) == []
    assert session.calls[1]["If-None-Match"] == ETAG and adapter.parses == 1


def test_real_transport_still_refuses_private_hosts(tracked, monkeypatch):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("127.0.0.1", port))])
    session = _Session(FakeServer())
    monkeypatch.setattr(http, "_thread_session", lambda: session)
    store.save_poll_validators("condtest", "s1", (LIST, ETAG, LAST_MOD))
    client = make_client("condtest", max_retries=0)
    client.transport = client._limited_transport
    with pytest.raises(http.UnsafeRedirect):
        chapter_check.check_series(ListAdapter(client), tracked)
    assert session.calls == []
