"""Regressions for web-source import: a learned next-link rule, the image
memory's chapter key, legacy CJK charsets and the chapter heading."""
import pytest

from sources import pipeline, profiles, store
from sources.ai_extract import PageModel
from sources.http_transport import decode_html
from sources.models import ChapterInfo


def _nav(n, with_prev):
    prev = f"<a href='c{n - 1}.html'>Previous</a>" if with_prev else ""
    return (f"<html><body><div class='nav'>{prev}<a href='c{n + 1}.html'>Next</a></div>"
            f"<div id='t'>text</div></body></html>")


def test_next_rule_learned_without_a_previous_link_follows_the_next_text():
    first = PageModel(_nav(1, False), "https://s.example/c1.html")
    rule = profiles._link_rule(first, "https://s.example/c2.html")
    later = PageModel(_nav(2, True), "https://s.example/c2.html")
    assert profiles._resolve_link(later, rule) == "https://s.example/c3.html"


def test_image_memory_ignores_url_variants_of_the_same_chapter(isolated_db):
    store.remember_images("a.example", "https://a.example/x", ["h1"])
    assert store.hashes_seen_elsewhere("a.example", "http://a.example/x/?from=toc#f", ["h1"]) == set()
    assert store.hashes_seen_elsewhere("a.example", "https://a.example/y", ["h1"]) == {"h1"}


@pytest.mark.parametrize("text,enc,label", [
    ("똠방각하", "cp949", "euc-kr"), ("똠방각하", "cp949", "ks_c_5601-1987"),
    ("①髙", "cp932", "shift_jis"), ("①髙", "cp932", "Shift-JIS"),
])
def test_vendor_extension_characters_survive_a_declared_legacy_charset(text, enc, label):
    raw = f"<p>{text}</p>".encode(enc)
    assert text in decode_html(raw, {"Content-Type": f"text/html; charset={label}"})


def test_chapter_text_starting_with_its_title_gets_one_heading(isolated_db):
    import db
    did = db.create_drama(title_en="D")
    ch = ChapterInfo(source="xbanxia", series_id="s1", chapter_id="c1", title="第一章")
    assert pipeline._append_chapter_text("xbanxia", ch, did, "第一章\n\n正文。") == ""
    from sources import chapter_manifest
    with open(chapter_manifest.raw_path(did), encoding="utf-8") as f:
        assert f.read().count("第一章") == 1
