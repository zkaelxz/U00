"""
tests/test_sources_translated_pages.py -- noticing a page the person's
own browser has already machine-translated, and two extraction fixes
found alongside it.

Why this matters: a browser translator *replaces* the source text in the
DOM rather than annotating it. Confirmed directly against a live Google
translation of a Japanese page -- afterwards the Japanese was simply gone.
So extracting from a translated page hands this app English to treat as
the original: it would "translate" English it believes is Chinese, or
store an English chapter as the source text. The import succeeds and the
text looks fine, which is exactly why it has to be said out loud.

The markup in these fixtures is the shape a real Google translation
produced, not an invented one -- nested `<font style="vertical-align:
inherit;">` wrappers, `translated-ltr` on `<html>`, and the `goog-gt-*`
elements injected at translation time.
"""
import pytest

from sources import detect

# What a real Google translation left behind (captured from a live run).
TRANSLATED_HTML = """<!doctype html><html lang="en" class="translated-ltr">
<head><title>chapter</title></head><body>
<div id="chapter">
  <p class="para"><font dir="auto" style="vertical-align: inherit;"><font dir="auto"
     style="vertical-align: inherit;">This is a sentence used to test translation
     detection.</font></font></p>
  <p class="para"><font dir="auto" style="vertical-align: inherit;"><font dir="auto"
     style="vertical-align: inherit;">The second paragraph is also here.</font></font></p>
</div>
<div id="goog-gt-tt"></div><div id="goog-gt-vt"></div>
</body></html>"""

# The same page before translating: the widget is embedded and idle, so
# `skiptranslate` and the element div are already present. This is the
# false positive the detector must not produce.
UNTRANSLATED_WITH_WIDGET = """<!doctype html><html lang="ja">
<head><title>chapter</title></head><body>
<div id="chapter">
  <p class="para">これは翻訳の検出を試すための文章です。</p>
  <p class="para">二番目の段落もここにあります。</p>
</div>
<div id="google_translate_element" class="skiptranslate"></div>
<div class="skiptranslate"></div>
</body></html>"""

PLAIN_HTML = """<html lang="zh"><head><title>x</title></head><body>
<div id="chapter"><p>第一段。</p><p>第二段。</p></div></body></html>"""


class TestNoticingATranslatedPage:
    def test_a_real_google_translated_page_is_recognised(self):
        found = detect.machine_translated(TRANSLATED_HTML)
        assert found, "a translated page must be recognised"
        assert any("Google Translate" in f for f in found)

    def test_an_idle_translate_widget_is_not_a_translated_page(self):
        """The widget being on the page is not the same as it having run.
        Matching `skiptranslate` or the element div would report every
        page that merely offers translation."""
        assert detect.machine_translated(UNTRANSLATED_WITH_WIDGET) == []

    def test_an_ordinary_page_is_left_alone(self):
        assert detect.machine_translated(PLAIN_HTML) == []
        assert detect.machine_translated("") == []
        assert detect.machine_translated(None) == []

    def test_the_html_class_alone_is_enough(self):
        """Chrome's own built-in translation sets this without embedding
        any widget, so it has to stand on its own."""
        assert detect.machine_translated('<html class="translated-ltr"><body>x</body></html>')
        assert detect.machine_translated("<html class='translated-rtl'><body>x</body></html>")

    def test_one_stray_font_tag_is_not_a_translation(self):
        """Old hand-written HTML has <font> tags. Only a page rewritten
        into several of them counts."""
        html = '<html><body><p><font style="vertical-align: inherit;">a</font></p></body></html>'
        assert detect.machine_translated(html) == []

    def test_the_edge_translator_is_recognised_too(self):
        html = '<html><body><p _msttexthash="123" _mstmutation="1">text</p></body></html>'
        found = detect.machine_translated(html)
        assert any("Edge" in f or "Microsoft" in f for f in found)

    def test_it_is_a_warning_not_an_access_failure(self):
        """The page loaded fine. Nothing here should stop the ladder or
        escalate a tier -- it must not become a FailureReason."""
        assert detect.classify(200, {}, TRANSLATED_HTML, "https://x.invalid/c/1") == []

    def test_it_is_recorded_as_evidence_for_the_attempt(self):
        ev = detect.evidence(200, {}, TRANSLATED_HTML, "https://x.invalid/c/1")
        assert ev["machine_translated"]
        assert detect.evidence(200, {}, PLAIN_HTML, "https://x.invalid/c/1")["machine_translated"] == []


class TestTheWarningReachesTheReport:
    def test_a_translated_page_warns_and_asks_for_review(self):
        from sources.adaptive import ExtractionReport, _note_if_translated
        report = ExtractionReport("https://x.invalid/c/1", "novel")
        _note_if_translated(report, TRANSLATED_HTML)
        assert report.needs_review is True
        joined = " ".join(report.lines)
        assert "already translated" in joined
        # It has to say what to actually do about it.
        assert "off" in joined.lower()

    def test_an_ordinary_page_produces_no_warning(self):
        from sources.adaptive import ExtractionReport, _note_if_translated
        report = ExtractionReport("https://x.invalid/c/1", "novel")
        _note_if_translated(report, PLAIN_HTML)
        assert report.needs_review is False
        assert report.lines == []


class TestTheShellConfidenceIsKept:
    """`looks_like_unrendered_shell` works out how confident it is and
    why; `classify` used only its boolean and dropped both. They're the
    closest thing here to a "is the text really in the DOM?" measure."""

    def test_confidence_and_reasons_are_recorded(self):
        shell = ("<html><head>" + "<script src='a.js'></script>" * 12 +
                 "</head><body><div id='root'></div></body></html>")
        ev = detect.evidence(200, {}, shell, "https://x.invalid/")
        assert ev["shell_confidence"] is not None
        assert ev["shell_confidence"] > 0
        assert ev["shell_reasons"]

    def test_a_normal_page_scores_low_and_still_reports(self):
        ev = detect.evidence(200, {}, PLAIN_HTML + "x" * 3000, "https://x.invalid/")
        assert ev["shell_confidence"] is not None


class TestParagraphsNestedOneLevelDeeper:
    """The direct-children scan misses a very ordinary shape: one wrapper
    element per paragraph. The outer container then scores zero, each
    inner one scores a single paragraph, and the winner is one paragraph
    -- the rest of the chapter dropped silently, because what comes back
    still looks like text."""

    def _chapter(self, paragraphs):
        inner = "".join(f"<div class='p-wrap'><p>{p}</p></div>" for p in paragraphs)
        return f"<html><body><div class='content'>{inner}</div></body></html>"

    def test_every_paragraph_is_recovered_not_just_one(self):
        from sources.generic_import import extract_main_text_heuristic
        paragraphs = [f"Paragraph number {i}. " + "word " * 40 for i in range(6)]
        text = extract_main_text_heuristic(self._chapter(paragraphs))
        for i in range(6):
            assert f"Paragraph number {i}." in text, f"paragraph {i} was dropped"

    def test_the_ordinary_flat_shape_still_works(self):
        """The fallback must not change pages that already worked."""
        from sources.generic_import import extract_main_text_heuristic
        body = "".join(f"<p>Flat paragraph {i}. " + "word " * 40 + "</p>" for i in range(5))
        text = extract_main_text_heuristic(f"<html><body><div class='content'>{body}</div></body></html>")
        for i in range(5):
            assert f"Flat paragraph {i}." in text

    def test_a_block_of_chapter_links_does_not_win_over_the_chapter(self):
        """Link text is discounted, so a dense chapter index can't be
        mistaken for the chapter."""
        from sources.generic_import import extract_main_text_heuristic
        links = "".join(f"<div><p><a href='/c/{i}'>Chapter {i} title here</a></p></div>"
                        for i in range(60))
        prose = "".join(f"<div><p>Real prose paragraph {i}. " + "word " * 30 + "</p></div>"
                        for i in range(5))
        html = (f"<html><body><div class='toc'>{links}</div>"
                f"<div class='content'>{prose}</div></body></html>")
        text = extract_main_text_heuristic(html)
        assert "Real prose paragraph 0." in text

    def test_an_empty_document_is_still_empty(self):
        from sources.generic_import import extract_main_text_heuristic
        assert extract_main_text_heuristic("<html><body></body></html>") == ""
        assert extract_main_text_heuristic("") == ""
