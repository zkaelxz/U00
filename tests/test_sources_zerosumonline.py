"""
tests/test_sources_zerosumonline.py -- Step 23l: the zerosumonline.com
adapter, against a real protobuf schema (field numbers read directly from
the real reference extension's own Dto.kt and independently confirmed by
decoding real, live captured API responses byte-for-byte while building
the adapter; see sources/adapters/zerosumonline.py's module docstring).
No request ever reaches the real site -- fixtures below are built with a
small test-only protobuf *encoder*, the exact inverse of the adapter's
own generic decoder, the same pattern this project's other custom-format
adapters use (see test_sources_baozimh.py's `_make_encoded_payload`).
"""
import pytest

from sources.adapters import zerosumonline
from sources.http import Response
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, make_client

BASE = zerosumonline.BASE_URL
API = f"https://api.{BASE.split('://', 1)[1]}/api/v1"


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7f
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field_no: int, wire_type: int) -> bytes:
    return _varint((field_no << 3) | wire_type)


def _str_field(field_no: int, s: str) -> bytes:
    data = s.encode("utf-8")
    return _tag(field_no, 2) + _varint(len(data)) + data


def _int_field(field_no: int, n: int) -> bytes:
    return _tag(field_no, 0) + _varint(n)


def _msg_field(field_no: int, payload: bytes) -> bytes:
    return _tag(field_no, 2) + _varint(len(payload)) + payload


def _api_title(slug, name, alt="", authors="", description="", thumbnail=""):
    body = _str_field(2, slug) + _str_field(3, name)
    if alt:
        body += _str_field(4, alt)
    if authors:
        body += _str_field(5, authors)
    if description:
        body += _str_field(7, description)
    if thumbnail:
        body += _str_field(8, thumbnail)
    return body


def _api_chapter(cid, name, published_at=0):
    return _int_field(1, cid) + _str_field(2, name) + _int_field(4, published_at)


def _title_list(*titles: bytes) -> bytes:
    return b"".join(_msg_field(3, t) for t in titles)


def _title_detail(title: bytes, *chapters: bytes) -> bytes:
    body = _msg_field(2, title)
    body += b"".join(_msg_field(3, c) for c in chapters)
    return body


def _viewer(*urls: str) -> bytes:
    return b"".join(_msg_field(5, _str_field(1, u)) for u in urls)


def _proto_response(payload: bytes) -> Response:
    # Real servers mislabel this content-type: application/json even
    # though the body is genuine protobuf (module docstring) -- the
    # fixture matches that.
    return Response(200, {"content-type": "application/json"}, payload, "")


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("zerosumonline", t, clock, max_retries=kw.pop("max_retries", 0))
    return zerosumonline.ZerosumOnlineSource(client=client, **kw), t


TITLE = _api_title("futsuoya", "Test Title", alt="Alt Title", authors="Author One",
                   description="A test description.",
                   thumbnail="https://img.example.invalid/cover.jpg")


class TestSearch:
    def test_empty_query_loads_the_series_list(self):
        titles = _title_list(TITLE)
        a, t = _adapter({f"{API}/list?category=series&sort=date": _proto_response(titles)})
        results = a.search("")
        assert len(results) == 1
        assert results[0].series_id == "futsuoya"
        assert results[0].title == "Test Title"
        assert results[0].cover_url == "https://img.example.invalid/cover.jpg"

    def test_query_hits_the_search_endpoint(self):
        import urllib.parse
        titles = _title_list(TITLE)
        url = f"{API}/search?keyword={urllib.parse.quote('futsuoya')}"
        a, t = _adapter({url: _proto_response(titles)})
        results = a.search("futsuoya")
        assert [r.series_id for r in results] == ["futsuoya"]
        assert t.urls() == [url]

    def test_no_results_is_an_empty_list_not_an_error(self):
        a, t = _adapter({f"{API}/list?category=series&sort=date": _proto_response(b"")})
        assert a.search("") == []


class TestSeries:
    def test_parses_title_altTitle_authors_description_cover(self):
        detail = _title_detail(TITLE)
        a, t = _adapter({f"{API}/title?tag=futsuoya": _proto_response(detail)})
        info = a.get_series("futsuoya")
        assert info.title == "Test Title"
        assert info.authors == ["Author One"]
        assert "A test description." in info.description
        assert "Alternative Title: Alt Title" in info.description
        assert info.cover_url == "https://img.example.invalid/cover.jpg"

    def test_layout_changed_when_title_record_is_missing(self):
        a, t = _adapter({f"{API}/title?tag=futsuoya": _proto_response(b"")})
        with pytest.raises(SourceError):
            a.get_series("futsuoya")


class TestChapters:
    def test_reverses_the_real_newest_first_order_to_ascending(self):
        # Confirmed against a real two-chapter series (module docstring):
        # the API returns newest-first.
        detail = _title_detail(TITLE, _api_chapter(3491, "二話", 1790305219),
                               _api_chapter(3492, "一話", 1790305218))
        a, t = _adapter({f"{API}/title?tag=futsuoya": _proto_response(detail)})
        chapters = a.get_chapters("futsuoya")
        assert [c.chapter_id for c in chapters] == ["3492", "3491"]
        assert [c.title for c in chapters] == ["一話", "二話"]

    def test_series_and_chapters_share_one_fetch(self):
        detail = _title_detail(TITLE, _api_chapter(1, "ch1"))
        a, t = _adapter({f"{API}/title?tag=futsuoya": _proto_response(detail)})
        a.get_series("futsuoya")
        a.get_chapters("futsuoya")
        assert len(t.calls) == 1

    def test_layout_changed_when_no_chapters(self):
        detail = _title_detail(TITLE)
        a, t = _adapter({f"{API}/title?tag=futsuoya": _proto_response(detail)})
        with pytest.raises(SourceError):
            a.get_chapters("futsuoya")


class TestPages:
    def test_extracts_page_urls_in_order(self):
        urls = ["https://img.example.invalid/pages/1.jpg", "https://img.example.invalid/pages/2.jpg"]
        viewer = _viewer(*urls)
        url = f"{API}/viewer?chapter_id=3491"
        a, t = _adapter({url: _proto_response(viewer)})
        chapter = ChapterInfo("zerosumonline", "futsuoya", "3491", "二話", f"{BASE}/detail/futsuoya")
        refs = a.get_pages(chapter)
        assert [r.url for r in refs] == urls
        assert t.calls[0]["method"] == "POST"

    def test_layout_changed_when_no_pages(self):
        url = f"{API}/viewer?chapter_id=3491"
        a, t = _adapter({url: _proto_response(b"")})
        chapter = ChapterInfo("zerosumonline", "futsuoya", "3491", "二話", f"{BASE}/detail/futsuoya")
        with pytest.raises(SourceError):
            a.get_pages(chapter)


class TestParseUrl:
    def test_series_url(self):
        a, t = _adapter({})
        kind, series_id = a.parse_url(f"{BASE}/detail/futsuoya")
        assert kind == "series"
        assert series_id == "futsuoya"

    def test_unrelated_url_is_not_claimed(self):
        a, t = _adapter({})
        assert a.parse_url("https://example.com/") is None


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url(f"{BASE}/detail/futsuoya")
        assert adapter is not None
        assert adapter.name == "zerosumonline"


class TestProtobufDecoderRobustness:
    def test_unknown_wire_type_is_reported_not_silently_dropped(self):
        # wire type 6/7 don't exist in real protobuf -- a byte stream
        # claiming one means the schema has genuinely changed.
        bad = _tag(1, 6)
        with pytest.raises(SourceError):
            zerosumonline._parse_protobuf(bad)

    def test_ignores_unmodeled_real_fields(self):
        # Field 1 and 6 are real wire fields on ApiTitle (confirmed
        # against live data) that the extension's own schema doesn't
        # model -- the decoder must not choke on them.
        payload = _int_field(1, 217) + _api_title("slug", "Name") + _str_field(6, "unmodeled")
        fields = zerosumonline._parse_protobuf(payload)
        assert zerosumonline._text(fields, 2) == "slug"
        assert zerosumonline._text(fields, 3) == "Name"
