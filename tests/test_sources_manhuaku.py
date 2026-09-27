"""
tests/test_sources_manhuaku.py -- Step 23j: the manhuaku.net adapter,
against recorded-shape fixtures only (series/chapter selectors checked
against the live site while building the adapter; see
sources/adapters/manhuaku.py's module docstring). No request ever reaches
the real site, and get_pages() is exercised only against a mocked
rendered-DOM fixture, never a raw-HTML one -- the roadmap's own explicit
exit condition for this step.
"""
import ast
import inspect
import textwrap

import pytest

from sources.adapters import manhuaku
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, image, make_client

BASE = manhuaku.BASE_URL

SERIES_PAGE = ("<html><body>"
              "<div class='cy_info_cover'><img src='https://img.example.invalid/cover.jpg'></div>"
              "<div class='cy_title'><h1>Test Manhua Title</h1></div>"
              "<div class='cy_xinxi'>"
              "<span class='cy_author'>作者：<a href='/search?key=test'>Test Author</a></span>"
              "<span class='cy_type'>类别：<a href='/category/tags/1'>Fantasy</a> </span>"
              "<span class='cy_serialize'>状态：<font color='#009900'>连载中</font></span>"
              "</div>"
              "<div class='cy_xinxi cy_desc'><p id='comic-description'>A test description.</p></div>"
              "<div class='cy_zhangjie_top'><ul id='mh-chapter-list-ol-0'>"
              "<li class='chapter__item'><a href='/chapter/ch3.html' title='第3话'><p>第3话</p></a></li>"
              "<li class='chapter__item'><a href='/chapter/ch2.html' title='第2话'><p>第2话</p></a></li>"
              "<li class='chapter__item'><a href='/chapter/ch1.html' title='第1话'><p>第1话</p></a></li>"
              "</ul></div>"
              "</body></html>")

EMPTY_SERIES_PAGE = "<html><body><div class='not-a-series-page'></div></body></html>"

RENDERED_CHAPTER_HTML = ("<html><body><div class='reader'>"
                        "<img src='https://img.example.invalid/pages/1.jpg'>"
                        "<img src='https://img.example.invalid/pages/2.jpg'>"
                        "<img src='https://img.example.invalid/pages/3.jpg'>"
                        "</div></body></html>")


def _adapter(routes, rendered_fetch=None, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("manhuaku", t, clock, max_retries=kw.pop("max_retries", 0))
    return manhuaku.ManhuakuSource(client=client, rendered_fetch=rendered_fetch, **kw), t


class TestSeries:
    def test_parses_title_author_genre_status_description_cover(self):
        a, t = _adapter({f"{BASE}/test-slug": html(SERIES_PAGE)})
        info = a.get_series("test-slug")
        assert info.title == "Test Manhua Title"
        assert info.authors == ["Test Author"]
        assert info.genres == ["Fantasy"]
        assert info.status == "ongoing"
        assert info.description == "A test description."
        assert info.cover_url == "https://img.example.invalid/cover.jpg"

    def test_layout_changed_when_title_is_missing(self):
        a, t = _adapter({f"{BASE}/test-slug": html(EMPTY_SERIES_PAGE)})
        with pytest.raises(SourceError):
            a.get_series("test-slug")


class TestChapters:
    def test_parses_chapters_in_ascending_order(self):
        a, t = _adapter({f"{BASE}/test-slug": html(SERIES_PAGE)})
        chapters = a.get_chapters("test-slug")
        assert [c.chapter_id for c in chapters] == ["ch1", "ch2", "ch3"]
        assert [c.title for c in chapters] == ["第1话", "第2话", "第3话"]

    def test_series_and_chapters_share_one_fetch(self):
        a, t = _adapter({f"{BASE}/test-slug": html(SERIES_PAGE)})
        a.get_series("test-slug")
        a.get_chapters("test-slug")
        assert len(t.calls) == 1


class TestGetPagesIsBrowserRenderedOnly:
    """The roadmap's own exit condition: get_pages() is mocked against a
    page_fetch.fetch_rendered-shaped fixture (a rendered DOM snapshot),
    never a raw-HTML one."""

    def test_extracts_pages_from_the_rendered_dom(self):
        image_urls = ["https://img.example.invalid/pages/1.jpg",
                      "https://img.example.invalid/pages/2.jpg",
                      "https://img.example.invalid/pages/3.jpg"]
        chapter_url = f"{BASE}/chapter/ch1.html"
        routes = {chapter_url: image(800, 1200, 1)}   # never fetched by get_pages() itself
        for i, u in enumerate(image_urls):
            routes[u] = image(800, 1200, 10 + i)
        rendered_fetch = lambda url: (RENDERED_CHAPTER_HTML, "")
        a, t = _adapter(routes, rendered_fetch=rendered_fetch)
        chapter = ChapterInfo("manhuaku", "test-slug", "ch1", "第1话", chapter_url)
        refs = a.get_pages(chapter)
        assert [r.url for r in refs] == image_urls
        # The chapter URL itself is never plain-HTTP GET'd for get_pages() --
        # only rendered (via the injected rendered_fetch) and the resulting
        # image URLs are downloaded.
        assert chapter_url not in t.urls()

    def test_layout_changed_when_the_rendered_page_has_no_images(self):
        chapter_url = f"{BASE}/chapter/ch1.html"
        rendered_fetch = lambda url: ("<html><body><p>nothing here</p></body></html>", "")
        a, t = _adapter({}, rendered_fetch=rendered_fetch)
        chapter = ChapterInfo("manhuaku", "test-slug", "ch1", "第1话", chapter_url)
        with pytest.raises(SourceError):
            a.get_pages(chapter)

    def test_refuses_clearly_when_pages_are_blob_urls(self):
        """Real, confirmed live behavior (2026-09-26), not hypothetical:
        this site's own readPic() writes real page images into the DOM as
        blob: object URLs, which only exist inside that one browser tab's
        memory and can never be independently re-downloaded. Before this
        fix, every real candidate silently failed to download and got
        filtered out, but the filter then kept unrelated leftover images
        (other titles' cover thumbnails from the page's own recommendation
        sidebar) instead of failing -- a live run actually returned those
        wrong images with no error. This must refuse instead."""
        rendered_html = ("<html><body><div class='reader'>"
                         "<img src='blob:https://www.manhuaku.net/11111111-1111-1111-1111-111111111111'>"
                         "<img src='https://img1.baipiaoguai.org/static/upload/book/cover/other.jpg'>"
                         "</div></body></html>")
        chapter_url = f"{BASE}/chapter/ch1.html"
        rendered_fetch = lambda url: (rendered_html, "")
        a, t = _adapter({}, rendered_fetch=rendered_fetch)
        chapter = ChapterInfo("manhuaku", "test-slug", "ch1", "第1话", chapter_url)
        with pytest.raises(SourceError) as exc_info:
            a.get_pages(chapter)
        assert "blob:" in str(exc_info.value)
        # The wrong-content bug this replaces: the unrelated cover image
        # must never be silently returned as a real page.
        assert not t.calls

    def test_resolves_blob_urls_when_the_render_step_captured_them(self):
        """The real fix: when the injected render function also returns a
        blob_bytes map (the 3-tuple fetch_rendered_resolving_blobs() shape,
        vs. the 2-tuple fetch_rendered() shape the other tests above use),
        a blob: candidate is served from its captured real bytes instead
        of being refused or re-downloaded over HTTP."""
        blob_url = "blob:https://www.manhuaku.net/11111111-1111-1111-1111-111111111111"
        rendered_html = (f"<html><body><div class='reader'>"
                         f"<img src='{blob_url}'>"
                         "</div></body></html>")
        chapter_url = f"{BASE}/chapter/ch1.html"
        blob_content = image(800, 1200, 1).content
        rendered_fetch = lambda url: (rendered_html, "", {blob_url: blob_content})
        a, t = _adapter({}, rendered_fetch=rendered_fetch)
        chapter = ChapterInfo("manhuaku", "test-slug", "ch1", "第1话", chapter_url)
        refs = a.get_pages(chapter)
        assert [r.url for r in refs] == [blob_url]
        # Never re-fetched over HTTP -- the bytes came from the render step.
        assert not t.calls
        content, ext = a.download_page(refs[0])
        assert content == blob_content
        assert ext == ".png"


class TestNoIndependentCryptoImplementation:
    """The roadmap's own exit condition: a structural check that
    get_pages() never imports or calls any AES/crypto-decryption code
    specific to this site, so the design decision can't quietly regress
    in a later edit."""

    _FORBIDDEN_NAMES = {"AES", "CryptoJS", "readPic", "decrypt", "Decrypt", "aes_decrypt"}

    def test_module_imports_no_crypto_library(self):
        """Naming the real protection (jsjiami, AES, CVE-2025-50234) in
        prose -- the module's own docstring and capabilities() text -- is
        expected and fine; what must never appear is an actual import of
        a decryption library. Checked on the parsed AST, not a plain
        string search, so honest documentation of the finding can't trip
        a naive text-based check."""
        tree = ast.parse(inspect.getsource(manhuaku))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported & {"Crypto", "Cryptodome", "cryptography", "pyaes", "AES"}

    def test_get_pages_ast_calls_no_crypto_looking_names(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(manhuaku.ManhuakuSource.get_pages)))
        called_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called_names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called_names.add(func.attr)
        assert not (called_names & self._FORBIDDEN_NAMES)


class TestSearchIsUnsupported:
    def test_search_is_not_offered(self):
        a, t = _adapter({})
        assert not a.supports("search")


class TestParseUrl:
    def test_chapter_url(self):
        a, t = _adapter({})
        kind, chapter = a.parse_url(f"{BASE}/chapter/ch1.html")
        assert kind == "chapter"
        assert chapter.chapter_id == "ch1"

    def test_series_url(self):
        a, t = _adapter({})
        kind, series_id = a.parse_url(f"{BASE}/test-slug")
        assert kind == "series"
        assert series_id == "test-slug"

    def test_unrelated_url_is_not_claimed(self):
        a, t = _adapter({})
        assert a.parse_url("https://example.com/") is None


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url(f"{BASE}/chapter/ch1.html")
        assert adapter is not None
        assert adapter.name == "manhuaku"
