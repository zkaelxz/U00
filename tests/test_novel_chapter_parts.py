"""A chapter a site splits over several pages (1.html, 1_2.html, ...) comes in
as one chapter: from a pasted chapter address, in a followed import, and from
a review of followed pages. Every fetch is scripted; no network."""
import os
from unittest import mock

import pytest

import background_jobs
import db
from services import sources_extraction_service as extraction
from services import sources_import_service as imp
from sources import ai_extract, novel_follow, pipeline

from .sources_helpers import ScriptedTransport, html as html_resp, make_client
from tests.job_helpers import wait_for_job

SITE = "https://www.shuge.example/1_1/"
_LINES = ["清晨的雾气还没有散去，小镇的石板路上湿漉漉的。", "林舟背着竹篓走出家门，远处传来卖豆腐的吆喝声。",
          "山路比想象中难走，露水打湿了他的裤脚。", "风从山谷里吹过来，他停下脚步想了很久。",
          "远处的钟声响了三下，她轻轻叹了一口气。", "门口的石阶上积了一层薄薄的青苔。"]


def page_url(chapter, part=1):
    return f"{SITE}{chapter}.html" if part == 1 else f"{SITE}{chapter}_{part}.html"


def page_html(chapter, part=1, parts=1, heading=None, next_word=None, next_href=None):
    """One page of chapter `chapter`; its next link is the chapter's next page
    ("下一页") until the last one, which links the next chapter ("下一章")."""
    title = f"第{chapter}章 初见" + (f"（第{part}/{parts}页）" if part > 1 else "")
    heading = title if heading is None else heading
    last = part == parts
    word = next_word or ("下一章" if last else "下一页")
    href = next_href or (f"/1_1/{chapter + 1}.html" if last else f"/1_1/{chapter}_{part + 1}.html")
    body = "".join(f"<br /><br />&nbsp;&nbsp;第{chapter}章第{part}页第{i}段。{_LINES[(i + part) % 6]}"
                   for i in range(8))
    h1 = f"<h1>{heading}</h1>" if heading else ""
    return (f"<html><head><title>{title}_测试之书_书阁</title></head><body>"
            f'<div class="bookname">{h1}<a href="/1_1/">章节目录</a> '
            f'<a href="{href}">{word}</a></div><div id="content">{body}</div>'
            f'<div class="bottem2"><a href="{href}">{word}</a></div></body></html>')


def routes_for(chapter, parts, **pages):
    routes = {page_url(chapter, p): html_resp(page_html(chapter, p, parts)) for p in range(1, parts + 1)}
    routes.update({u: html_resp(h) for u, h in pages.items()})
    return routes


def follow(routes, start=page_url(1), max_pages=10, **kw):
    transport = ScriptedTransport(routes)
    client = make_client("generic", transport)
    out = novel_follow.follow_novel(start, max_pages, client=client, allow_browser=False,
                                    allow_signed_in=False, **kw)
    return out, transport


# ---------------------------------------------------------------------------
# Telling a chapter's next page from the next chapter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,bare", [
    ("第一章 初见（第2/2页）", "第一章 初见"),
    ("第一章 初见(2/3)", "第一章 初见"),
    ("第一章 初见 第2页", "第一章 初见"),
    ("第一章 初见_第3頁", "第一章 初见"),
    ("Chapter 3: The Road (Page 2 of 3)", "Chapter 3: The Road"),
    ("第一章 初见", "第一章 初见"),
    # A part number that isn't a page is the site's own chapter title.
    ("第十章 旅途（上）", "第十章 旅途（上）"),
    ("第十章 旅途(2)", "第十章 旅途(2)"),
])
def test_chapter_heading_takes_off_only_a_page_marker(title, bare):
    assert novel_follow.chapter_heading(title) == bare


@pytest.mark.parametrize("link,yes", [
    (f"{SITE}1_2.html", True),
    (f"{SITE}1-3.html", True),
    (f"{SITE}1.html?page=2", True),
    ("https://shuge.example/1_1/1_2.html", True),        # "www." doesn't make another site
    (f"{SITE}2.html", False),
    (f"{SITE}12.html", False),
    (f"{SITE}1_2_3.html", False),
    (f"{SITE}1.html?sort=2", False),
    ("https://other.example/1_1/1_2.html", False),
])
def test_part_address(link, yes):
    assert novel_follow._is_part_url(link, page_url(1)) is yes


def test_the_extractor_keeps_a_next_page_link_and_the_next_chapter_link_after_it():
    assert ai_extract.link_resemblance(page_url(1, 2), page_url(1))[0] > 0.8
    assert ai_extract.link_resemblance(page_url(2), page_url(1, 2))[0] > 0.8
    assert ai_extract.link_resemblance(f"{SITE}forum/thread-5.html", page_url(1, 2))[0] == 0


# ---------------------------------------------------------------------------
# follow_novel marks a chapter's later pages
# ---------------------------------------------------------------------------

def test_followed_pages_mark_a_chapters_later_pages(isolated_db):
    routes = routes_for(1, 3)
    routes.update(routes_for(2, 1))
    out, t = follow(routes, max_pages=4)
    assert [p.url for p in out.pages] == [page_url(1), page_url(1, 2), page_url(1, 3), page_url(2)]
    assert [p.part for p in out.pages] == [False, True, True, False]
    chapters = novel_follow.joined_chapters([(p.text, p.title, p.url, p.part) for p in out.pages])
    assert [(h, u, n) for _t, h, u, n in chapters] == [("第1章 初见", page_url(1), 3),
                                                      ("第2章 初见", page_url(2), 1)]
    text = chapters[0][0]
    assert text.index("第1章第1页第0段") < text.index("第1章第2页第0段") < text.index("第1章第3页第7段")
    assert "第2章" not in text


def test_parts_only_never_requests_the_next_chapter(isolated_db):
    routes = routes_for(1, 2)
    routes.update(routes_for(2, 1))
    out, t = follow(routes, parts_only=True)
    assert out.stop == "no_next" and [p.part for p in out.pages] == [False, True]
    assert t.urls() == [page_url(1), page_url(1, 2)]


def test_parts_only_drops_a_next_page_link_that_leads_to_another_chapter(isolated_db):
    # Some sites call the next chapter "下一页" too: its heading tells.
    first = page_html(1, next_word="下一页", next_href="/1_1/2.html")
    out, t = follow({page_url(1): html_resp(first), page_url(2): html_resp(page_html(2))},
                    parts_only=True)
    assert out.stop == "no_next" and len(out.pages) == 1
    assert t.urls() == [page_url(1), page_url(2)]


def test_a_part_without_a_heading_is_known_by_its_address(isolated_db):
    second = page_html(1, 2, 2, heading="")
    out, _t = follow({page_url(1): html_resp(page_html(1, 1, 2)), page_url(1, 2): html_resp(second)},
                     parts_only=True)
    assert [p.part for p in out.pages] == [False, True]


def test_a_query_page_number_alone_never_joins_two_headingless_chapters(isolated_db):
    # read.php?p=1 -> ?p=2 may be the next chapter on a site that numbers chapters that way.
    def headingless(n, word):
        return page_html(n, heading="", next_word=word, next_href=f"/1_1/read.php?p={n + 1}")
    for word, joined in (("next", False), ("下一页", True)):
        start = f"{SITE}read.php?p=1"
        routes = {start: html_resp(headingless(1, word)),
                  f"{SITE}read.php?p=2": html_resp(headingless(2, "下一章"))}
        out, _t = follow(routes, start=start, max_pages=2)
        assert [p.part for p in out.pages] == [False, joined], word


def test_parts_only_stops_at_the_cap_and_the_usual_checks(isolated_db):
    out, t = follow(routes_for(1, 5), max_pages=3, parts_only=True)
    assert out.stop == "cap" and len(out.pages) == 3
    out, t = follow(routes_for(1, 2), parts_only=True, url_check=lambda u: u == page_url(1))
    assert out.stop == "not_public" and t.urls() == [page_url(1)]
    out, t = follow({page_url(1): html_resp(page_html(1, 1, 2))}, parts_only=True)   # part 2: 404
    assert out.stop == "unreachable" and len(out.pages) == 1


def test_joined_chapters_never_joins_across_a_page_left_out():
    pages = [("一", "第1章 初见", "u1", False), ("二", "第1章 初见（第2/3页）", "u2", True),
             ("三", "第1章 初见（第3/3页）", "u3", True), ("四", "第2章", "u4", False)]
    assert novel_follow.joined_chapters(pages) == [("一\n二\n三", "第1章 初见", "u1", 3),
                                                   ("四", "第2章", "u4", 1)]
    assert novel_follow.joined_chapters([None, pages[1], pages[2], None]) == [
        ("二\n三", "第1章 初见", "u2", 2)]
    assert novel_follow.joined_chapters([pages[0], None, pages[2]]) == [
        ("一", "第1章 初见", "u1", 1), ("三", "第1章 初见（第3/3页）", "u3", 1)]


# ---------------------------------------------------------------------------
# A pasted chapter address
# ---------------------------------------------------------------------------

def _raw(did):
    path = os.path.join(db.drama_dir(did), pipeline.RAW_NOVEL_FILENAME)
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def _url_import(routes, did):
    transport = ScriptedTransport(routes)
    results = []
    with mock.patch.object(imp, "source_client", return_value=make_client("generic", transport)), \
            mock.patch.object(imp, "_is_public", return_value=True), \
            mock.patch.object(background_jobs, "set_result",
                              side_effect=lambda jid, r: results.append(r)), \
            mock.patch.object(background_jobs, "update_progress"), \
            mock.patch.object(background_jobs, "is_cancel_requested", return_value=False):
        imp._url_import_job(f"sourceimport_{did}", page_url(1), did, local=False)
    return results[-1], transport


def test_url_import_saves_a_split_chapter_as_one_chapter(isolated_db):
    did = db.create_drama(title_en="N", media_type="novel")
    routes = routes_for(1, 3)
    routes.update(routes_for(2, 1))
    result, t = _url_import(routes, did)
    assert result["needs_review"] is False
    assert t.urls() == [page_url(1), page_url(1, 2), page_url(1, 3)]
    raw = _raw(did)
    assert raw.startswith("第1章 初见\n") and "页）" not in raw and "第2章" not in raw
    assert raw.index("第1章第1页第7段") < raw.index("第1章第2页第0段") < raw.index("第1章第3页第0段")
    one_page = len(novel_follow.follow_novel(page_url(1), 1, client=make_client(
        "generic", ScriptedTransport(routes)), allow_browser=False, allow_signed_in=False).first.text)
    assert result["char_count"] > 2 * one_page


def test_url_import_opens_a_review_when_a_later_page_wont_load(isolated_db):
    did = db.create_drama(title_en="N", media_type="novel")
    routes = routes_for(1, 3)
    del routes[page_url(1, 3)]
    result, _t = _url_import(routes, did)
    assert result["needs_review"] is True and result["review_open"] is True
    assert result["pages_found"] == 2 and result["follow_stop"] == "unreachable"
    assert _raw(did) == ""
    view = extraction.review_view(did)
    assert [p["title"] for p in view["follow"]["pages"]] == ["第1章 初见", "第1章 初见（第2/3页）"]


def test_url_import_of_an_unsplit_chapter_reads_one_page(isolated_db):
    did = db.create_drama(title_en="N", media_type="novel")
    result, t = _url_import(routes_for(1, 1), did)
    assert result == {"kind": "url_import", "needs_review": False,
                      "char_count": result["char_count"], "review_open": False}
    assert t.urls() == [page_url(1)] and _raw(did).startswith("第1章 初见\n")


# ---------------------------------------------------------------------------
# A review of followed pages
# ---------------------------------------------------------------------------


def _review_import(did, pages=None):
    rv = extraction.review_view(did)
    extraction.start_review_import(did, rv["revision"], pages=pages)
    st = wait_for_job(f"sourceimport_{did}", missing_ok=True)
    background_jobs.clear_job(f"sourceimport_{did}")
    return st["result"]


@pytest.mark.parametrize("chosen,chapters,pages_imported", [
    (None, ["第1章 初见", "第2章 初见"], 3),
    ([1, 2], ["第1章 初见（第2/2页）", "第2章 初见"], 2),        # its chapter page left out
])
def test_review_import_joins_a_chapters_later_pages(isolated_db, chosen, chapters, pages_imported):
    did = db.create_drama(title_en="N", media_type="novel")
    routes = routes_for(1, 2)
    routes.update(routes_for(2, 1))
    out, _t = follow(routes, max_pages=3)
    assert extraction.open_review(did, "novel", page_url(1), out.first.ladder.html, out.report.data,
                                  out.report, extraction.WHY_FOLLOWED, chain=out.pages[1:],
                                  follow_stop=out.stop)
    result = _review_import(did, chosen)
    assert result["pages_imported"] == pages_imported
    raw = _raw(did)
    heads = [line for line in raw.splitlines() if line.startswith("第") and "段" not in line]
    assert heads == chapters


def test_paragraph_batches_end_at_a_paragraph_end_and_never_exceed_the_size():
    from types import SimpleNamespace as L
    from engine_backends.translate_pipeline import plan_paragraph_batches
    lines = [L(idx=i) for i in range(10)]
    batches = plan_paragraph_batches(lines, 6, {2, 4, 9})
    assert [[ln.idx for ln in b] for b in batches] == [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9]]
    assert plan_paragraph_batches(lines, 4, set()) == [lines[0:4], lines[4:8], lines[8:10]]


def test_novel_titles_start_with_wider_context_windows():
    import translate_engines
    novel = translate_engines.default_translate_options(novel=True)
    assert (novel["context_window"], novel["context_window_ahead"]) == (30, 15)
    plain = translate_engines.default_translate_options()
    assert (plain["context_window"], plain["context_window_ahead"]) == (10, 6)
