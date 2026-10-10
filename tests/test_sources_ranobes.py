"""
tests/test_sources_ranobes.py -- Step 91: the ranobes.net adapter, against
recorded-shape fixtures only. No request ever reaches the real site.
"""
import pytest

from sources.adapters import ranobes
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = ranobes.BASE_URL

SEARCH_RESULTS_PAGE = (
    "<html><body>"
    "<article class='block story shortstory mod-poster'>"
    "<div class='short-cont'><h2 class='title'>"
    "<a href='https://ranobes.net/novels/2001-test-novel.html'>Test Novel</a></h2></div>"
    "</article>"
    "<article class='block story shortstory mod-poster'>"
    "<div class='short-cont'><h2 class='title'>"
    "<a href='https://ranobes.net/novels/2002-another-novel.html'>Another Novel</a></h2></div>"
    "</article>"
    "</body></html>")

SERIES_PAGE = (
    "<html><body>"
    "<h1 class='title'>Test Novel <span class='subtitle'>by Test Author</span></h1>"
    "<div class='r-fullstory-poster'><figure class='cover' "
    "style=\"background-image: url(https://ranobes.net/uploads/2001.jpg);\"></figure></div>"
    "<div class='moreless__short'>Short blurb...</div>"
    "<div class='moreless__full'>The full real description of the novel.</div>"
    "</body></html>")

SERIES_PAGE_NO_TITLE = "<html><body><div class='not-a-title'></div></body></html>"

CHAPTERS_PAGE_1 = (
    "<html><body><div class='chapters__container'></div>"
    "<script>window.__DATA__ = {\"book_title\":\"Test Novel\",\"book_id\":2001,"
    "\"chapters\":[{\"id\":\"3001\",\"title\":\"Chapter 2\",\"link\":"
    "\"https://ranobes.net/test-novel-2001/3001.html\"},"
    "{\"id\":\"3000\",\"title\":\"Chapter 1\",\"link\":"
    "\"https://ranobes.net/test-novel-2001/3000.html\"}],"
    "\"pages_count\":2,\"cstart\":1};</script></body></html>")

CHAPTERS_PAGE_2 = (
    "<html><body><div class='chapters__container'></div>"
    "<script>window.__DATA__ = {\"book_title\":\"Test Novel\",\"book_id\":2001,"
    "\"chapters\":[{\"id\":\"2999\",\"title\":\"Chapter 0 (prologue)\",\"link\":"
    "\"https://ranobes.net/test-novel-2001/2999.html\"}],"
    "\"pages_count\":2,\"cstart\":2};</script></body></html>")

CHAPTERS_PAGE_NO_DATA = "<html><body>No data blob here.</body></html>"

CHAPTER_PAGE = ("<html><body><div class='text' id='arrticle'>"
                "<p>First paragraph.</p>"
                "<p>Second paragraph with <strong>emphasis</strong> inline.</p>"
                "</div></body></html>")

CHAPTER_PAGE_EMPTY = "<html><body><div class='text' id='arrticle'></div></body></html>"

CHAPTER_PAGE_NO_CONTAINER = "<html><body><div class='not-it'></div></body></html>"


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("ranobes", t, clock, max_retries=kw.pop("max_retries", 0))
    return ranobes.RanobesSource(client=client, **kw), t


class TestSearch:
    def test_posts_to_the_dle_search_action(self):
        a, t = _adapter({f"{BASE}/index.php?do=search": html(SEARCH_RESULTS_PAGE)})
        results = a.search("test novel")
        assert t.calls[0]["method"] == "POST"
        assert t.calls[0]["data"]["do"] == "search"
        assert t.calls[0]["data"]["subaction"] == "search"
        assert t.calls[0]["data"]["story"] == "test novel"

    def test_parses_results_and_series_ids(self):
        a, t = _adapter({f"{BASE}/index.php?do=search": html(SEARCH_RESULTS_PAGE)})
        results = a.search("test")
        assert [r.title for r in results] == ["Test Novel", "Another Novel"]
        assert results[0].series_id == "novels/2001-test-novel.html"
        assert results[0].url == "https://ranobes.net/novels/2001-test-novel.html"

    def test_no_results(self):
        a, t = _adapter({f"{BASE}/index.php?do=search": html("<html><body></body></html>")})
        assert a.search("nothing") == []


class TestSeries:
    def test_parses_title_author_description_and_cover(self):
        a, t = _adapter({f"{BASE}/novels/2001-test-novel.html": html(SERIES_PAGE)})
        info = a.get_series("novels/2001-test-novel.html")
        assert info.title == "Test Novel"
        assert info.authors == ["Test Author"]
        assert info.description == "The full real description of the novel."
        assert info.cover_url == "https://ranobes.net/uploads/2001.jpg"

    def test_layout_changed_when_title_is_missing(self):
        a, t = _adapter({f"{BASE}/novels/2001-test-novel.html": html(SERIES_PAGE_NO_TITLE)})
        with pytest.raises(SourceError):
            a.get_series("novels/2001-test-novel.html")


class TestChapters:
    def test_walks_every_page_and_collects_the_embedded_data_blob(self):
        a, t = _adapter({
            f"{BASE}/chapters/2001/": html(CHAPTERS_PAGE_1),
            f"{BASE}/chapters/2001/page/2/": html(CHAPTERS_PAGE_2),
        })
        chapters = a.get_chapters("novels/2001-test-novel.html")
        assert [c.chapter_id for c in chapters] == ["3001", "3000", "2999"]
        assert [c.title for c in chapters] == ["Chapter 2", "Chapter 1", "Chapter 0 (prologue)"]
        assert chapters[0].url == "https://ranobes.net/test-novel-2001/3001.html"

    def test_stops_after_one_page_when_pages_count_is_one(self):
        single = CHAPTERS_PAGE_1.replace('"pages_count":2', '"pages_count":1')
        a, t = _adapter({f"{BASE}/chapters/2001/": html(single)})
        chapters = a.get_chapters("novels/2001-test-novel.html")
        assert len(chapters) == 2
        assert len(t.calls) == 1

    def test_a_huge_pages_count_is_capped(self, monkeypatch):
        monkeypatch.setattr(ranobes, "MAX_LIST_PAGES", 3)
        huge = CHAPTERS_PAGE_1.replace('"pages_count":2', '"pages_count":1000000000')
        a, t = _adapter({
            f"{BASE}/chapters/2001/": html(huge),
            f"{BASE}/chapters/2001/page/2/": html(CHAPTERS_PAGE_2),
            f"{BASE}/chapters/2001/page/3/": html(CHAPTERS_PAGE_2),
        })
        a.get_chapters("novels/2001-test-novel.html")
        assert len(t.calls) == 3

    def test_layout_changed_when_no_data_blob(self):
        a, t = _adapter({f"{BASE}/chapters/2001/": html(CHAPTERS_PAGE_NO_DATA)})
        with pytest.raises(SourceError):
            a.get_chapters("novels/2001-test-novel.html")


class TestChapterText:
    def test_extracts_paragraphs_with_word_boundaries_preserved(self):
        a, t = _adapter({f"{BASE}/test-novel-2001/3001.html": html(CHAPTER_PAGE)})
        chapter = ChapterInfo("ranobes", "novels/2001-test-novel.html", "3001", "Chapter 2",
                              f"{BASE}/test-novel-2001/3001.html")
        text = a.get_chapter_text(chapter)
        assert "First paragraph." in text
        # Inline <strong> must not mash adjacent words together.
        assert "Second paragraph with emphasis inline." in text

    def test_layout_changed_when_container_is_empty(self):
        a, t = _adapter({f"{BASE}/x/1.html": html(CHAPTER_PAGE_EMPTY)})
        chapter = ChapterInfo("ranobes", "novels/2001.html", "1", "x", f"{BASE}/x/1.html")
        with pytest.raises(SourceError):
            a.get_chapter_text(chapter)

    def test_layout_changed_when_container_is_missing(self):
        a, t = _adapter({f"{BASE}/x/1.html": html(CHAPTER_PAGE_NO_CONTAINER)})
        chapter = ChapterInfo("ranobes", "novels/2001.html", "1", "x", f"{BASE}/x/1.html")
        with pytest.raises(SourceError):
            a.get_chapter_text(chapter)


class TestParseUrl:
    def _adapter(self):
        return ranobes.RanobesSource(client=make_client("ranobes", ScriptedTransport({}, FakeClock()), FakeClock()))

    def test_series_url(self):
        a = self._adapter()
        kind, series_id = a.parse_url("https://ranobes.net/novels/2001-test-novel.html")
        assert (kind, series_id) == ("series", "novels/2001-test-novel.html")

    def test_chapter_url(self):
        a = self._adapter()
        kind, chapter = a.parse_url("https://ranobes.net/test-novel-2001/3001.html")
        assert kind == "chapter"
        assert chapter.series_id == "novels/2001.html"
        assert chapter.chapter_id == "3001"

    def test_unrelated_url_is_not_matched(self):
        a = self._adapter()
        assert a.parse_url("https://example.com/whatever") is None


class TestMatchesUrl:
    def test_matches_series_and_chapter_shapes(self):
        assert ranobes.RanobesSource.matches_url("https://ranobes.net/novels/2001-test-novel.html")
        assert ranobes.RanobesSource.matches_url("https://ranobes.net/test-novel-2001/3001.html")
        assert not ranobes.RanobesSource.matches_url("https://ranobes.top/novels/2001-test-novel.html")


class TestCapabilities:
    def test_records_the_domain_and_search_findings(self):
        a, t = _adapter({})
        caps = a.capabilities()
        assert "ranobes.top" in caps.technical["domain_note"]
        assert caps.terms["tos_prohibited"] is False


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://ranobes.net/novels/2001-test-novel.html")
        assert adapter is not None
        assert adapter.name == "ranobes"


class TestSiteTermsForUnbuiltCandidates:
    """Step 91 also investigated wuxiaworld.com and webnovel.com but built
    no adapter for either (a real client-fetched paywalled text pipeline,
    and a live Cloudflare challenge, respectively) -- their findings still
    need to be recorded per this project's site_terms.py convention."""

    def test_wuxiaworld_findings_are_recorded(self):
        from sources import site_terms
        caps = site_terms.capabilities_for("https://www.wuxiaworld.com/novel/x")
        assert caps is not None
        assert caps.automation_permission == "EXPLICITLY_RESTRICTED"
        assert caps.terms["tos_prohibited"] is True
        assert "scraper" in caps.terms["clause"].lower()

    def test_webnovel_findings_are_recorded(self):
        from sources import site_terms
        caps = site_terms.capabilities_for("https://www.webnovel.com/book/x")
        assert caps is not None
        assert "cloudflare" in caps.terms["extraction_method"].lower()
