"""
tests/test_sources_bilibili_manga.py -- Step 23f: the Bilibili Manga
adapter. Everything here is mocked/recorded fixtures -- no live Bilibili
call, no real Playwright render, matching this repo's convention.

Covers the roadmap's two explicit automated exit conditions:
  - the SourceCapabilities record reports browser_accessible=true, the
    confirmed token-based protection, and UNTESTED for every
    authentication-dependent state;
  - the adapter never calls the ImageToken endpoint directly -- every
    image URL it downloads comes from a mocked "already-rendered page"
    fixture, token query string and all, unmodified.
"""
import importlib

import pytest

bilibili_manga = importlib.import_module("sources.adapters.bilibili_manga")

from sources.models import ChapterInfo, ContentHidden, FailureReason

from .sources_helpers import FakeClock, ScriptedTransport, html, image, make_client

CHAPTER_URL = "https://manga.bilibili.com/detail/mc28793/1000012345"

# A real series page (manga.bilibili.com/detail/mc28793) returns
# essentially <div id="app-vm"></div> plus a <noscript> notice -- no
# server-side content leakage at all (module docstring). Padded past 2000
# chars via an HTML comment so page_fetch.looks_like_unrendered_shell's
# text-to-markup ratio check fires the same way it would on the real page.
STATIC_SHELL_HTML = (
    '<html><head><title>哔哩哔哩漫画</title></head><body>'
    '<div id="app"></div>'
    '<noscript>请开启 JavaScript 以正常访问本页面</noscript>'
    '<!-- ' + ("filler " * 320) + '-->'
    '</body></html>')

# Real, already-signed image URLs as they'd appear in the rendered DOM --
# the adapter must reuse these exactly, never construct its own.
TOKEN_IMG_1 = "https://images.hdslb.com/bfs/manga-static/abc/page1.jpg?token=sig111aaa"
TOKEN_IMG_2 = "https://images.hdslb.com/bfs/manga-static/abc/page2.jpg?token=sig222bbb"

RENDERED_CHAPTER_HTML = (
    '<html><body><div class="manga-reader">'
    f'<img src="{TOKEN_IMG_1}">'
    f'<img src="{TOKEN_IMG_2}">'
    '</div></body></html>')

RENDERED_NO_IMAGES_HTML = "<html><body><p>无法加载本页面内容</p></body></html>"


def _adapter(routes, rendered_fetch, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("bilibili_manga", t, clock, max_retries=kw.pop("max_retries", 0))
    return bilibili_manga.BilibiliMangaSource(
        client=client, rendered_fetch=rendered_fetch, **kw), t


def _chapter(url=CHAPTER_URL, chapter_id="1000012345"):
    return ChapterInfo("bilibili_manga", "", chapter_id, chapter_id, url)


class TestGetPagesReusesRenderedTokens:
    def test_falls_back_from_the_empty_static_shell_to_the_rendered_page(self, isolated_db):
        routes = {CHAPTER_URL: html(STATIC_SHELL_HTML),
                 TOKEN_IMG_1: image(800, 1200, 1),
                 TOKEN_IMG_2: image(800, 1200, 2)}
        rendered_fetch = lambda url: (RENDERED_CHAPTER_HTML, "")
        a, t = _adapter(routes, rendered_fetch)
        refs = a.get_pages(_chapter())
        assert [r.url for r in refs] == [TOKEN_IMG_1, TOKEN_IMG_2]
        assert [r.index for r in refs] == [0, 1]

    def test_never_calls_an_image_token_endpoint_directly(self, isolated_db):
        """The adapter must only ever download image URLs exactly as they
        appeared in the rendered page -- token query string included,
        unmodified -- never a URL it built itself against a token API."""
        routes = {CHAPTER_URL: html(STATIC_SHELL_HTML),
                 TOKEN_IMG_1: image(800, 1200, 1),
                 TOKEN_IMG_2: image(800, 1200, 2)}
        rendered_fetch = lambda url: (RENDERED_CHAPTER_HTML, "")
        a, t = _adapter(routes, rendered_fetch)
        a.get_pages(_chapter())
        called = t.urls()
        assert called == [CHAPTER_URL, TOKEN_IMG_1, TOKEN_IMG_2]
        assert not any("imagetoken" in u.lower() for u in called)
        assert not any("getimageindex" in u.lower() for u in called)

    def test_download_page_returns_the_bytes_get_pages_already_fetched(self, isolated_db):
        routes = {CHAPTER_URL: html(STATIC_SHELL_HTML),
                 TOKEN_IMG_1: image(800, 1200, 1),
                 TOKEN_IMG_2: image(800, 1200, 2)}
        rendered_fetch = lambda url: (RENDERED_CHAPTER_HTML, "")
        a, t = _adapter(routes, rendered_fetch)
        refs = a.get_pages(_chapter())
        content, ext = a.download_page(refs[0])
        assert content and ext == ".png"

    def test_download_page_fails_once_the_cached_bytes_are_used_up(self, isolated_db):
        routes = {CHAPTER_URL: html(STATIC_SHELL_HTML),
                 TOKEN_IMG_1: image(800, 1200, 1),
                 TOKEN_IMG_2: image(800, 1200, 2)}
        rendered_fetch = lambda url: (RENDERED_CHAPTER_HTML, "")
        a, t = _adapter(routes, rendered_fetch)
        refs = a.get_pages(_chapter())
        a.download_page(refs[0])
        with pytest.raises(ContentHidden) as e:
            a.download_page(refs[0])
        assert e.value.reason == FailureReason.SIGNED_RESOURCE

    def test_content_hidden_when_the_rendered_page_has_no_images_either(self, isolated_db):
        routes = {CHAPTER_URL: html(STATIC_SHELL_HTML)}
        rendered_fetch = lambda url: (RENDERED_NO_IMAGES_HTML, "无法加载本页面内容")
        a, t = _adapter(routes, rendered_fetch)
        with pytest.raises(ContentHidden) as e:
            a.get_pages(_chapter())
        assert e.value.reason == FailureReason.SIGNED_RESOURCE


class TestSearchSeriesChaptersAreUnsupported:
    def test_none_of_the_browsing_methods_are_offered(self):
        a, t = _adapter({}, lambda url: ("", ""))
        assert not a.supports("search")
        assert not a.supports("get_series")
        assert not a.supports("get_chapters")


class TestParseUrl:
    def test_detail_mc_id_slash_chapter_form(self):
        a, t = _adapter({}, lambda url: ("", ""))
        kind, chapter = a.parse_url(CHAPTER_URL)
        assert kind == "chapter"
        assert chapter.chapter_id == "1000012345"
        assert chapter.url == CHAPTER_URL

    def test_short_mc_id_slash_chapter_form(self):
        a, t = _adapter({}, lambda url: ("", ""))
        url = "https://manga.bilibili.com/mc28793/1000012345"
        kind, chapter = a.parse_url(url)
        assert kind == "chapter"
        assert chapter.chapter_id == "1000012345"

    def test_episode_url_with_a_from_query_is_recognized(self):
        a = bilibili_manga.BilibiliMangaSource()
        kind, ch = a.parse_url("https://manga.bilibili.com/mc40738/2129714?from=manga_detail")
        assert kind == "chapter" and ch.chapter_id == "2129714"

    def test_a_series_only_url_does_not_match(self):
        a, t = _adapter({}, lambda url: ("", ""))
        assert a.parse_url("https://manga.bilibili.com/detail/mc28793") is None

    def test_an_unrelated_url_does_not_match(self):
        a, t = _adapter({}, lambda url: ("", ""))
        assert a.parse_url("https://example.com/") is None


class TestCapabilities:
    def test_reports_browser_accessible_and_the_confirmed_token_protection(self):
        a, t = _adapter({}, lambda url: ("", ""))
        caps = a.capabilities()
        assert caps.technical["browser_accessible"] is True
        assert "token" in caps.technical["protection_detected"].lower()
        assert "never calls the imagetoken api" in caps.technical["extraction_method"].lower()

    def test_every_authentication_dependent_state_is_untested_not_guessed(self):
        a, t = _adapter({}, lambda url: ("", ""))
        caps = a.capabilities()
        assert caps.status == "UNTESTED"
        assert caps.technical_status == "UNRESOLVED"
        assert caps.terms["checked"] is False
        assert caps.terms["tos_prohibited"] is False

    def test_agreement_urls_are_recorded_for_the_pending_manual_read(self):
        a, t = _adapter({}, lambda url: ("", ""))
        caps = a.capabilities()
        assert len(caps.terms["agreement_urls"]) == 4
        assert all(u.startswith("https://manga.bilibili.com/") for u in caps.terms["agreement_urls"])


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url(CHAPTER_URL)
        assert adapter is not None
        assert adapter.name == "bilibili_manga"


class TestEmptyShellMessages:
    def test_browser_tier_that_still_sees_a_shell_says_so(self, isolated_db):
        from sources import ladder
        from sources.models import AccessTier

        def shell(url):
            return STATIC_SHELL_HTML, ""
        out = ladder.rendered_tier(None, shell)("https://manga.bilibili.com/mc1/2")
        assert not out.ok and "browser tier also returned an empty page" in out.detail
