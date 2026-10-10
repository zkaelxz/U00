"""
tests/test_sources_kuaikan.py -- Step 23i: the Kuaikan Manhua adapter,
against a synthetic-but-grammar-accurate window.__NUXT__ fixture (a
small, hand-built stand-in for the same dedup-IIFE shape confirmed
against three independent real pages while building this adapter -- see
sources/adapters/kuaikan.py's module docstring). No request ever reaches
the real site, and nothing here executes any JavaScript -- the fixture
exercises the same bounded literal-plus-identifier parser the adapter
itself uses.
"""
import time

import pytest

from sources.adapters import kuaikan
from sources.models import ChapterInfo, ContentHidden, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = kuaikan.BASE_URL


def _nuxt_html(payload) -> str:
    """Builds a real window.__NUXT__=(function(a,b,...){return
    {...}}(argA,argB,...)) dedup-IIFE around `payload`, in the same shape
    the adapter's own _decode_nuxt_state() parses -- a test-only encoder,
    never used by the adapter itself. Each distinct leaf value gets its
    own parameter (no attempt at real deduplication -- the parser doesn't
    care whether a value is actually shared)."""
    params = []
    args = []

    def alloc(js_literal):
        name = f"v{len(params)}"
        params.append(name)
        args.append(js_literal)
        return name

    def encode(value):
        if isinstance(value, str):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return alloc(f'"{escaped}"')
        if isinstance(value, bool):
            return alloc("true" if value else "false")
        if value is None:
            return alloc("null")
        if isinstance(value, (int, float)):
            return alloc(str(value))
        if isinstance(value, list):
            items = ",".join(encode(v) for v in value)
            return alloc(f"[{items}]")
        if isinstance(value, dict):
            items = ",".join(f"{k}:{encode(v)}" for k, v in value.items())
            return alloc(f"{{{items}}}")
        raise TypeError(type(value))

    # The top-level payload is always a dict on a real page, and it's
    # always inlined directly after `return` rather than referenced by a
    # dedup identifier -- match that shape so _decode_nuxt_state()'s
    # `return\s*\{` anchor finds it, the same as on a real page.
    assert isinstance(payload, dict)
    root_items = ",".join(f"{k}:{encode(v)}" for k, v in payload.items())
    func_params = ",".join(params)
    call_args = ",".join(args)
    script = f"(function({func_params}){{return {{{root_items}}}}}({call_args}))"
    return f"<html><body><script>window.__NUXT__={script};</script></body></html>"


SERIES_PAYLOAD = {
    "data": [{
        "topicInfo": {
            "id": 10189, "title": "Test Series Title",
            "cover_image_url": "https://img.example.invalid/cover.jpg",
            "description": "A test series description.",
            "tags": ["Fantasy", "Urban"],
            "user": {"nickname": "Test Author"},
            "update_status": "连载中",
        },
        "comics": [
            {"id": 1001, "title": "Chapter 0", "locked": False},
            {"id": 1002, "title": "Chapter 1", "locked": False},
            {"id": 1003, "title": "Chapter 2 (locked)", "locked": True},
        ],
    }],
}

CHAPTER_PAYLOAD = {
    "data": [{
        "comicInfo": {
            "id": 1001, "title": "Chapter 0", "locked": False,
            "comicImages": [
                {"url": "https://img.example.invalid/c1001/1.jpg?sign=aaa"},
                {"url": "https://img.example.invalid/c1001/2.jpg?sign=bbb"},
            ],
        },
    }],
}

LOCKED_CHAPTER_PAYLOAD = {
    "data": [{"comicInfo": {"id": 1003, "title": "Chapter 2 (locked)",
                           "locked": True, "comicImages": []}}],
}


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("kuaikan", t, clock, max_retries=kw.pop("max_retries", 0))
    return kuaikan.KuaikanSource(client=client, **kw), t


class TestNuxtStateDecoder:
    """Confirms the bounded literal-plus-identifier parser round-trips
    the exact shapes real pages were confirmed to use while building this
    adapter: nested objects/arrays, and the "populate a placeholder
    container via separate mutation statements" dedup pattern (checked
    directly here, since the encode() helper above doesn't produce that
    shape -- it's exercised via a hand-written fixture instead)."""

    def test_decodes_nested_objects_and_arrays(self):
        html_text = _nuxt_html({"a": 1, "b": ["x", "y", {"c": True, "d": None}]})
        state = kuaikan._decode_nuxt_state(html_text[html_text.index("window"):
                                                      html_text.index("</script>")])
        assert state == {"a": 1, "b": ["x", "y", {"c": True, "d": None}]}

    def test_decodes_the_placeholder_mutation_pattern(self):
        # (function(a,b,c){az=a;az[0]=b;az[1]=c;return {tags:az}}(Array(2),"x","y"))
        raw = ('(function(az,b,c){az[0]=b;az[1]=c;return {tags:az}}(Array(2),"x","y"))')
        state = kuaikan._decode_nuxt_state("window.__NUXT__=" + raw + ";")
        assert state == {"tags": ["x", "y"]}

    def test_layout_changed_when_no_nuxt_state_is_present(self):
        with pytest.raises(SourceError):
            kuaikan._extract_nuxt_state("<html><body>nothing here</body></html>")


def _state_page(params, body, args):
    return (f"<script>window.__NUXT__=(function({params}){{{body}}}({args}));</script>")


class TestNuxtStateDecoderBounds:
    @pytest.mark.parametrize("count", ["2e9", "2000000000", "10001", "-1", "1.5"])
    def test_refuses_an_unreasonable_array_size(self, count):
        with pytest.raises(kuaikan.LayoutChanged):
            kuaikan._extract_nuxt_state(_state_page("a", "return {x:a}", f"Array({count})"))

    def test_largest_allowed_array_still_decodes(self):
        state = kuaikan._extract_nuxt_state(_state_page("a", "return {x:a}", "Array(10000)"))
        assert len(state["x"]) == 10_000

    def test_many_arrays_share_one_budget(self):
        n = kuaikan._MAX_ARRAY_CELLS // kuaikan._MAX_ARRAY_LEN + 1
        body = "return {x:[" + ",".join(["Array(10000)"] * n) + "]}"
        with pytest.raises(kuaikan.LayoutChanged):
            kuaikan._extract_nuxt_state(_state_page("a", body, "1"))

    def test_deep_nesting_is_a_layout_error(self):
        body = "return {x:" + "[" * 100_000 + "]" * 100_000 + "}"
        with pytest.raises(kuaikan.LayoutChanged):
            kuaikan._extract_nuxt_state(_state_page("a", body, "1"))

    def test_arguments_referring_to_each_other_are_a_layout_error(self):
        with pytest.raises(kuaikan.LayoutChanged):
            kuaikan._extract_nuxt_state(_state_page("a,b", "return {x:a}", "b,a"))

    def test_hostile_megabyte_is_linear(self):
        n = 50_000
        params = ",".join(f"p{i}" for i in range(n))
        args = ",".join(str(i) for i in range(n))
        body = "return {" + ",".join(f"k{i}:p{i}" for i in range(n)) + "}"
        page = _state_page(params, body, args)
        assert len(page) > 1_000_000
        t = time.perf_counter()
        state = kuaikan._extract_nuxt_state(page)
        assert time.perf_counter() - t < 2.0
        assert state["k49999"] == 49_999


class TestSeries:
    def test_parses_title_description_tags_author(self):
        a, t = _adapter({f"{BASE}/web/topic/10189": html(_nuxt_html(SERIES_PAYLOAD))})
        info = a.get_series("10189")
        assert info.title == "Test Series Title"
        assert info.description == "A test series description."
        assert info.genres == ["Fantasy", "Urban"]
        assert info.authors == ["Test Author"]
        assert info.status == "ongoing"
        assert info.cover_url == "https://img.example.invalid/cover.jpg"


class TestChapters:
    """The roadmap's own exit condition (adjusted for a real, confirmed
    finding): catalog/chapter-listing extraction works against a mocked
    real SSR HTML fixture, with no browser-tier mock needed at all --
    stronger than the roadmap's original ask, which expected only the
    *catalog* half to avoid the browser tier (see the module docstring
    for why cover-image resolution needs no browser tier here either)."""

    def test_parses_every_chapter_in_order_from_the_embedded_state(self):
        a, t = _adapter({f"{BASE}/web/topic/10189": html(_nuxt_html(SERIES_PAYLOAD))})
        chapters = a.get_chapters("10189")
        assert [c.title for c in chapters] == ["Chapter 0", "Chapter 1", "Chapter 2 (locked)"]
        assert [c.chapter_id for c in chapters] == ["1001", "1002", "1003"]

    def test_series_and_chapters_share_one_fetch(self):
        a, t = _adapter({f"{BASE}/web/topic/10189": html(_nuxt_html(SERIES_PAYLOAD))})
        a.get_series("10189")
        a.get_chapters("10189")
        assert len(t.calls) == 1


class TestPages:
    def test_get_pages_returns_real_signed_image_urls(self):
        a, t = _adapter({f"{BASE}/web/comic/1001": html(_nuxt_html(CHAPTER_PAYLOAD))})
        chapter = ChapterInfo("kuaikan", "10189", "1001", "Chapter 0", f"{BASE}/web/comic/1001")
        refs = a.get_pages(chapter)
        assert [r.url for r in refs] == ["https://img.example.invalid/c1001/1.jpg?sign=aaa",
                                         "https://img.example.invalid/c1001/2.jpg?sign=bbb"]

    def test_locked_chapter_raises_content_hidden_not_a_layout_error(self):
        a, t = _adapter({f"{BASE}/web/comic/1003": html(_nuxt_html(LOCKED_CHAPTER_PAYLOAD))})
        chapter = ChapterInfo("kuaikan", "10189", "1003", "Chapter 2", f"{BASE}/web/comic/1003")
        with pytest.raises(ContentHidden):
            a.get_pages(chapter)


class TestSearchIsUnsupported:
    def test_search_is_not_offered(self):
        a, t = _adapter({})
        assert not a.supports("search")


class TestParseUrl:
    def test_series_url(self):
        a, t = _adapter({})
        kind, series_id = a.parse_url(f"{BASE}/web/topic/10189")
        assert kind == "series"
        assert series_id == "10189"

    def test_chapter_url(self):
        a, t = _adapter({})
        kind, chapter = a.parse_url(f"{BASE}/web/comic/1001")
        assert kind == "chapter"
        assert chapter.chapter_id == "1001"

    def test_reader_redirect_url_also_matches(self):
        a, t = _adapter({})
        kind, chapter = a.parse_url(f"{BASE}/webs/comic-next/1001")
        assert kind == "chapter"
        assert chapter.chapter_id == "1001"

    def test_unrelated_url_is_not_claimed(self):
        a, t = _adapter({})
        assert a.parse_url("https://example.com/") is None


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url(f"{BASE}/web/topic/10189")
        assert adapter is not None
        assert adapter.name == "kuaikan"
