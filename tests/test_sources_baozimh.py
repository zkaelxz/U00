"""
tests/test_sources_baozimh.py -- Step 23i: the baozimh.org/godamh.com
adapter, against recorded-shape fixtures only (all shapes -- including a
full, real image-decode payload -- checked against the live site while
building the adapter; see sources/adapters/baozimh.py's module docstring).
No request ever reaches the real site.
"""
import json

import pytest

from sources.adapters import baozimh
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

MIRRORS = baozimh.MIRRORS

DETAIL_HTML = ("<html><body><main>"
              "<div id='mangachapters' data-mid='12'></div>"
              "<div><div class='gap-2'>"
              "<h1> Test Manhua Title <span>連載中</span></h1>"
              "</div>"
              "<div class='text-sm py-1 pb-2'>"
              "<span class='font-medium'>作者：</span>"
              "<a href='/x'><span>Author One , </span></a>"
              "<a href='/y'><span>Author Two </span></a>"
              "</div>"
              "<div class='text-sm py-1'>"
              "<span class='font-medium'>類型：</span>"
              "<a href='/g1'><span>Adventure , </span></a>"
              "<a href='/g2'><span>Fantasy </span></a>"
              "</div>"
              "<div class='py-1'>"
              "<a href='/t1'><span>#古风</span></a>"
              "<a href='/t2'><span>#热血</span></a>"
              "</div>"
              "<p class='text-sm text-foreground/90 line-clamp-4 my-4'>A test description.</p>"
              "</div>"
              "<img class='object-cover' src='https://img.example.invalid/cover.jpg'>"
              "</main></body></html>")

EMPTY_DETAIL_HTML = "<html><body><main></main></body></html>"

# The real API returns chapters newest-first (confirmed against the live
# site while building this adapter) -- the fixture matches that ordering,
# and get_chapters() reverses it to the ascending order this app expects.
CHAPTERS_JSON = json.dumps({"data": {"id": 12, "slug": "test-manhua",
                                     "chapters": [
                                         {"id": 101, "attributes": {"title": "Chapter 2",
                                                                    "slug": "0_1",
                                                                    "updatedAt": "2024-01-02T00:00:00Z"}},
                                         {"id": 100, "attributes": {"title": "Chapter 1",
                                                                    "slug": "0_0",
                                                                    "updatedAt": "2024-01-01T00:00:00Z"}},
                                     ]}})


def _make_encoded_payload(image_urls):
    """Builds a real ChapterImageDecoder.decode()-compatible payload the
    same way the site's own client-side encoder would, for a fixture --
    the inverse of ChapterImageDecoder.decode(), used only to build test
    data (never used by the adapter itself)."""
    import base64
    D = baozimh.ChapterImageDecoder
    items = [{"url": u, "order": i + 1} for i, u in enumerate(image_urls)]
    plain = json.dumps(items).encode("utf-8")
    b64url = base64.urlsafe_b64encode(plain).decode("ascii").rstrip("=")
    # Map standard base64url alphabet -> the site's custom alphabet (the
    # exact inverse of ChapterImageDecoder's own decode table).
    encode_table = {v: k for k, v in D._DECODE_TABLE.items()}
    mapped = "".join(encode_table[c] for c in b64url)
    # Zigzag: reverse every second consecutive 7-char block.
    zigzagged = []
    for block, i in enumerate(range(0, len(mapped), D.GROUP)):
        chunk = mapped[i:i + D.GROUP]
        zigzagged.append(chunk[::-1] if block % 2 == 1 else chunk)
    zigzagged = "".join(zigzagged)
    a_len = len(zigzagged) // 3
    # Split zigzagged into (part3, part1, part2) matching decode()'s own
    # reordering, then undo it: reordered = part3+part1+part2, so
    # part3 = zigzagged[:a_len], and the rest splits between part1/part2
    # by the same b_len/c_len rule decode() uses.
    payload_len = len(zigzagged)
    b_len = (payload_len - a_len) // 2
    part3 = zigzagged[:a_len]
    part1 = zigzagged[a_len:a_len + b_len]
    part2 = zigzagged[a_len + b_len:]
    body = part1 + D.MARKER1 + part2 + D.MARKER2 + part3
    return D.PREFIX + body + D.SUFFIX


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("baozimh", t, clock, max_retries=kw.pop("max_retries", 0))
    return baozimh.BaozimhSource(client=client, **kw), t


class TestSeries:
    def test_parses_title_status_author_genre_description(self):
        a, t = _adapter({f"{MIRRORS[0]}/manga/test-manhua": html(DETAIL_HTML)})
        info = a.get_series("test-manhua")
        assert info.title == "Test Manhua Title"
        assert info.status == "ongoing"
        assert info.authors == ["Author One", "Author Two"]
        assert info.genres == ["Adventure", "Fantasy", "古风", "热血"]
        assert info.description == "A test description."
        assert info.cover_url == "https://img.example.invalid/cover.jpg"

    def test_layout_changed_when_detail_is_missing(self):
        a, t = _adapter({f"{MIRRORS[0]}/manga/test-manhua": html(EMPTY_DETAIL_HTML)})
        with pytest.raises(SourceError):
            a.get_series("test-manhua")


class TestChapters:
    def test_parses_chapters_from_the_json_api_in_ascending_order(self):
        a, t = _adapter({
            f"{MIRRORS[0]}/manga/test-manhua": html(DETAIL_HTML),
            f"{baozimh.API_BASE}/api/manga/get?mid=12&mode=all": html(CHAPTERS_JSON),
        })
        chapters = a.get_chapters("test-manhua")
        assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]
        assert [c.chapter_id for c in chapters] == ["100", "101"]

    def test_chapter_urls_use_the_mirror_that_was_actually_active(self):
        """If the primary mirror is down and the series page was actually
        fetched from a fallback mirror, chapter URLs must point at that
        same fallback -- never hardcode the first mirror in the list."""
        import requests
        routes = {
            f"{MIRRORS[0]}/manga/test-manhua": requests.ConnectionError("down"),
            f"{MIRRORS[1]}/manga/test-manhua": html(DETAIL_HTML),
            f"{baozimh.API_BASE}/api/manga/get?mid=12&mode=all": html(CHAPTERS_JSON),
        }
        a, t = _adapter(routes)
        chapters = a.get_chapters("test-manhua")
        assert len(chapters) == 2
        assert all(c.url.startswith(MIRRORS[1]) for c in chapters)
        assert not any(c.url.startswith(MIRRORS[0]) for c in chapters)


class TestPageImageDecoding:
    """The roadmap's own exit condition: correctly resolve a sample
    ChapterImageDecoder-style payload to real image URLs."""

    def test_decoder_round_trips_a_sample_payload(self):
        urls = ["/scomic/test/1.webp", "/scomic/test/2.webp", "/scomic/test/3.webp"]
        payload = _make_encoded_payload(urls)
        decoded = baozimh.ChapterImageDecoder.decode(payload)
        items = sorted(json.loads(decoded), key=lambda it: it["order"])
        assert [it["url"] for it in items] == urls

    def test_get_pages_returns_real_image_urls_in_order(self):
        urls = ["/scomic/test/1.webp", "/scomic/test/2.webp"]
        payload = _make_encoded_payload(urls)
        page_json = json.dumps({"data": {"info": {"images": {"images": payload}}}})
        a, t = _adapter({
            f"{MIRRORS[0]}/manga/test-manhua": html(DETAIL_HTML),
            f"{baozimh.API_BASE}/api/v2/chapter/getinfo?m=12&c=100": html(page_json),
        })
        chapter = ChapterInfo("baozimh", "test-manhua", "100", "Chapter 1",
                              f"{MIRRORS[0]}/manga/test-manhua/0_0")
        refs = a.get_pages(chapter)
        assert [r.url for r in refs] == [baozimh.IMAGE_HOST + u for u in urls]

    def test_layout_changed_on_an_unrecognized_payload_shape(self):
        with pytest.raises(SourceError):
            baozimh.ChapterImageDecoder.decode("not-a-real-payload")


class TestMirrorFallback:
    def test_a_failed_primary_mirror_falls_back_to_the_next(self):
        import requests
        routes = {
            f"{MIRRORS[0]}/manga/test-manhua": requests.ConnectionError("down"),
            f"{MIRRORS[1]}/manga/test-manhua": html(DETAIL_HTML),
        }
        a, t = _adapter(routes)
        info = a.get_series("test-manhua")
        assert info.title == "Test Manhua Title"
        assert t.urls() == [f"{MIRRORS[0]}/manga/test-manhua", f"{MIRRORS[1]}/manga/test-manhua"]


class TestParseUrl:
    def test_series_url(self):
        a, t = _adapter({})
        kind, series_id = a.parse_url(f"{MIRRORS[0]}/manga/test-manhua")
        assert kind == "series"
        assert series_id == "test-manhua"

    def test_unrelated_url_is_not_claimed(self):
        a, t = _adapter({})
        assert a.parse_url("https://example.com/") is None


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://baozimh.org/manga/test-manhua")
        assert adapter is not None
        assert adapter.name == "baozimh"
