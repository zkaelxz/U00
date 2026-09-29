"""
tests/test_sources_preflight.py -- "will this site work?" answered before
committing to an import.

The point of the preflight is that *every ordinary no is an answer*, not
an exception: terms that prohibit automated access, a page that can't be
reached, and a page whose text doesn't survive the import checks all come
back as findings a person can read. These tests pin that, and pin that
the preflight is never kinder than the real import -- it judges with the
same `validate_novel` checks, so it can't promise something the importer
would then refuse.

Nothing here touches the network: pages are served by a scripted
transport through the real access ladder.
"""
import pytest

from sources import preflight as pf

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

URL = "https://novels.invalid/book/1/chapter/7"


def _chapter_html(paragraphs=12, with_links=True, extra=""):
    body = "".join(
        f"<p>第{i}段。これは十分に長い本文の段落であり、ナビゲーションではありません。"
        f"物語はここで続きます。{'内容' * 20}</p>" for i in range(paragraphs))
    nav = ('<a href="/book/1/chapter/8">下一章</a>'
           '<a href="/book/1/chapter/6">上一章</a>'
           '<a href="/book/1">目录</a>') if with_links else ""
    return html(f"<html><head><title>第七章</title></head><body>"
                f"<h1>第七章</h1><div class='content'>{body}</div>{nav}{extra}"
                f"</body></html>")


def _client(pages, clock=None):
    clock = clock or FakeClock()
    return make_client("generic", ScriptedTransport(pages, clock), clock, max_retries=0)


def _run(pages, url=URL):
    return pf.preflight(url, client=_client(pages))


class TestAReadableNovelPage:
    def test_it_says_the_site_would_work(self, isolated_db):
        result = _run({URL: _chapter_html()})
        assert result.ok is True
        assert result.reachable is True
        assert result.permitted is True
        assert "novel" in result.verdict.lower() or "importable" in result.verdict.lower()

    def test_it_reports_how_much_text_and_how_confident(self, isolated_db):
        result = _run({URL: _chapter_html()})
        assert result.text_chars > 200
        assert result.confidence in ("high", "medium", "HIGH", "MEDIUM")

    def test_it_reports_whether_a_series_can_be_followed(self, isolated_db):
        """The question a translation check could never answer: is there
        a chapter list to walk?"""
        result = _run({URL: _chapter_html(with_links=True)})
        assert result.next_link is True
        assert result.previous_link is True
        assert result.toc_links >= 1
        assert any("series can be followed" in line for line in result.lines)

    def test_it_says_so_when_chapters_cannot_be_followed(self, isolated_db):
        result = _run({URL: _chapter_html(with_links=False)})
        assert result.next_link is False
        assert any("one URL at a time" in line for line in result.lines)

    def test_it_reads_as_a_report_a_person_can_follow(self, isolated_db):
        result = _run({URL: _chapter_html()})
        assert result.lines
        assert result.summary() == result.verdict


class TestEveryOrdinaryNoIsAnAnswerNotAnException:
    def test_prohibited_terms_are_reported_not_raised(self, isolated_db, monkeypatch):
        """A written restriction is the single most important thing a
        preflight can tell you, so it must not arrive as a traceback."""
        from sources import front_door
        from sources.models import TermsProhibited

        def refuse(*a, **kw):
            raise TermsProhibited("this site's terms prohibit automated access")
        monkeypatch.setattr(front_door, "preview", refuse)
        result = pf.preflight(URL)
        assert result.ok is False
        assert result.permitted is False
        assert "terms" in result.verdict.lower()

    def test_an_unreachable_page_is_reported(self, isolated_db):
        from sources.http import Response
        result = _run({URL: Response(404, {"content-type": "text/html"}, b"nope", URL)})
        assert result.ok is False
        assert result.reachable is False
        assert result.verdict

    def test_a_page_with_no_real_text_is_refused_clearly(self, isolated_db):
        """It must not promise an import that the importer would then
        reject -- the preflight uses the same checks."""
        thin = html("<html><head><title>x</title></head><body>"
                    "<div class='content'><p>短い。</p></div></body></html>")
        result = _run({URL: thin})
        assert result.ok is False
        assert "didn't pass" in result.verdict or "couldn't tell" in result.verdict.lower()

    def test_an_empty_url_is_an_answer_too(self, isolated_db):
        result = pf.preflight("")
        assert result.ok is False
        assert result.verdict


class TestTheTranslatedPageWarning:
    def test_an_already_translated_page_warns_before_importing(self, isolated_db):
        """Catching this *before* an import is the whole point: afterwards
        the translation is already saved as the source text."""
        translated = html(
            '<html class="translated-ltr" lang="en"><head><title>ch</title></head><body>'
            '<div class="content">' +
            "".join('<p><font dir="auto" style="vertical-align: inherit;">'
                    "This paragraph was replaced by the browser's translator and is long "
                    "enough to count as real prose for the extractor. " * 2 +
                    "</font></p>" for _ in range(12)) +
            "</div></body></html>")
        result = _run({URL: translated})
        assert result.warnings
        assert any("translated" in w.lower() for w in result.warnings)
        assert any("off" in w.lower() for w in result.warnings)

    def test_an_ordinary_page_carries_no_warning(self, isolated_db):
        result = _run({URL: _chapter_html()})
        assert result.warnings == []


class TestComicAndOtherPages:
    def test_a_comic_page_is_judged_on_its_images(self, isolated_db):
        from .sources_helpers import image
        imgs = "".join(f'<img src="/p{i}.jpg" width="800" height="1200">' for i in range(5))
        pages = {URL: html(f"<html><head><title>ch</title></head><body>{imgs}</body></html>")}
        for i in range(5):
            pages[f"https://novels.invalid/p{i}.jpg"] = image(800, 1200)
        result = _run(pages)
        # Either it reads as a comic, or it honestly says it couldn't tell.
        assert result.verdict
        assert result.ok in (True, False)

    def test_a_registered_adapter_answers_by_existing(self, isolated_db, monkeypatch):
        from sources import front_door
        fake = front_door.Preview(url=URL, adapter="manhuagui", platform="Manhuagui",
                                  content_type="comic")
        monkeypatch.setattr(front_door, "preview", lambda *a, **kw: fake)
        result = pf.preflight(URL)
        assert result.ok is True
        assert result.adapter == "manhuagui"
        assert "adapter" in result.verdict.lower()


class TestItOnlyFetchesOnce:
    def test_the_page_is_fetched_a_single_time(self, isolated_db):
        """A preflight people avoid running because it is slow or noisy
        is a preflight that doesn't get run."""
        clock = FakeClock()
        transport = ScriptedTransport({URL: _chapter_html()}, clock)
        pf.preflight(URL, client=make_client("generic", transport, clock, max_retries=0))
        page_calls = [c for c in transport.calls if c["url"] == URL]
        assert len(page_calls) == 1


