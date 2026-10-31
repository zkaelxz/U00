"""
tests/test_novel_import_quality.py -- how well a novel comes in: every saved case in
tests/fixtures/novel_import/ (site pages, TXT and EPUB uploads, chapter OCR) run
through the app's own import and scored by tools/novel_import_quality.py, plus the
shared cleanup rules in novel_text.py. A site change that breaks an adapter, or a
change that lets site junk or a wrong encoding back in, fails the case's own test.
"""
import pytest

import novel_text
from tools import novel_import_quality as quality

# Per case: the lowest acceptable score for each measure, and the most chapters
# missing / duplicated / left-over lines allowed. Raise a floor when a case improves;
# never lower one to get green.
PERFECT = {"scores": dict.fromkeys(quality.SCORE_KEYS, 1.0), "missing": 0, "duplicates": 0,
           "junk_lines": 0, "extra": 0}
# A TXT file's title and blurb before chapter 1 are listed as a block of their own.
WITH_FRONT_MATTER = {**PERFECT, "extra": 1}
FLOORS = {
    "52shuku": PERFECT,
    "epub_ja": PERFECT,
    "epub_zh": PERFECT,
    "generic_en": PERFECT,
    "generic_zh": PERFECT,
    "generic_zh_paged": PERFECT,
    "lightnovel_fun": PERFECT,
    "ocr_zh": PERFECT,
    "piaotian_gbk": PERFECT,
    "ranobes": PERFECT,
    "syosetu_ruby": PERFECT,
    "txt_big5": WITH_FRONT_MATTER,
    "txt_gbk": WITH_FRONT_MATTER,
    "txt_sjis": WITH_FRONT_MATTER,
    "xbanxia": PERFECT,
}


def test_every_saved_case_has_a_floor():
    assert sorted(FLOORS) == quality.case_names()


@pytest.mark.usefixtures("isolated_db")
@pytest.mark.parametrize("case", sorted(FLOORS))
def test_case_meets_its_floor(case):
    floor = FLOORS[case]
    result = quality.run_case(case)
    low = {k: v for k, v in result["scores"].items() if v < floor["scores"][k]}
    assert not low, f"{case}: {low} (leftover lines: {result['junk_examples']})"
    for key in ("missing", "duplicates", "extra"):
        got = result[key] if isinstance(result[key], int) else len(result[key])
        assert got <= floor[key], f"{case}: {key} {result[key]}"
    assert result["junk_lines"] <= floor["junk_lines"], result["junk_examples"]


# -- the scorer can fail ------------------------------------------------------------

EXPECTED = [{"title": "第一章 甲", "paragraphs": ["一段。", "二段。"]},
            {"title": "第二章 乙", "paragraphs": ["三段。", "四段。"]}]


def _ch(title, *lines):
    return {"title": title, "text": "\n".join((title,) + lines)}


def test_score_counts_missing_duplicate_and_out_of_order_chapters():
    got = quality.score(EXPECTED, [_ch("第二章 乙", "三段。", "四段。"),
                                   _ch("第二章 乙", "三段。", "四段。")])
    assert got["missing"] == ["第一章 甲"] and got["duplicates"] == 1
    got = quality.score(EXPECTED, [_ch("第二章 乙", "三段。", "四段。"),
                                   _ch("第一章 甲", "一段。", "二段。")])
    assert got["scores"]["order"] == 0.5


def test_score_counts_junk_lost_paragraphs_and_wrong_encoding():
    got = quality.score(EXPECTED, [_ch("第一章 甲", "一段。", "记住本站"),
                                   _ch("第二章 乙", "三段。", "å››æ®µã€‚")])
    assert got["junk_lines"] == 2 and got["scores"]["paragraphs"] == 0.5
    assert got["damaged_chars"] > 0 and got["scores"]["encoding"] < 1


def test_score_reads_an_unsplit_book_as_one_chapter():
    got = quality.score(EXPECTED, [_ch("Unsplit text", "第一章 甲", "一段。", "二段。",
                                       "第二章 乙", "三段。", "四段。")])
    assert got["scores"]["completeness"] == 0.5 and got["scores"]["titles"] == 0


# -- the shared cleanup rules --------------------------------------------------------

@pytest.mark.parametrize("line", [
    "天才一秒记住本站地址：www.example-shuge.com",
    "请记住本书首发域名：example.com。书阁手机版阅读网址：m.example.com",
    "上一章 ← 章节目录 → 下一章 加入书签",
    "Previous Chapter | Table of Contents | Next Chapter",
    "|",
    "本章未完，点击下一页继续阅读》》",
    "example-site.com",
])
def test_site_chrome_lines_are_dropped(line):
    assert novel_text.is_site_chrome_line(line)


@pytest.mark.parametrize("line", [
    "他打开手机，搜索信封上那个旧网址 www.example.com 的记录，却什么也没有找到。",
    "“下一章写什么？”他对着空白的稿纸发呆。",
    "\"Next!\" the keeper shouted over the wind.",
    "I visited example.com yesterday and it was fun.",
    "She added it to her bookmark at fanfic.net before bed.",
    "＊　＊　＊",
    "——",
    "Next",
    "Contents:",
    "目录",
])
def test_prose_that_mentions_a_site_or_the_next_chapter_is_kept(line):
    assert not novel_text.is_site_chrome_line(line)


def test_ocr_text_loses_cjk_spaces_and_rejoins_wrapped_lines_across_pages():
    pages = "第 三 章 旧 信\n雨 停 之 后 ， 他\n发 现 了 信 。\n\n她 说 ： “ 等\n我 回 来 。 ”"
    assert novel_text.tidy_ocr_text(pages).split("\n") == [
        "第三章旧信", "雨停之后，他发现了信。", "她说：“等我回来。”"]


def test_ocr_text_drops_page_numbers_and_ends_a_paragraph_at_a_dash():
    assert novel_text.tidy_ocr_text("他 说\n— 12 —\n好 。\n她 转 身 走 了 ——\n雨 停 了 。").split("\n") == [
        "他说好。", "她转身走了——", "雨停了。"]


def test_ocr_text_keeps_latin_word_spaces():
    assert novel_text.tidy_ocr_text("The ferry left\nthe harbour late.") == \
        "The ferry left the harbour late."


def test_chapter_starts_skips_a_contents_page_and_needs_two_chapters():
    book = "目录\n第一章 甲\n第二章 乙\n\n第一章 甲\n一段。\n第二章 乙\n二段。\n"
    starts = novel_text.chapter_starts(book)
    assert [t for _o, t in starts] == ["第一章 甲", "第二章 乙"]
    assert book[starts[0][0]:].startswith("第一章 甲\n一段。")
    assert novel_text.chapter_starts("第一章 甲\n一段。\n") == []


def test_chapter_starts_keeps_a_volume_heading_with_its_first_chapter():
    book = "第一卷 风起\n第一章 开始\n一段。\n第二章 空\n第三章 后来\n二段。\n"
    starts = novel_text.chapter_starts(book)
    assert [t for _o, t in starts] == ["第一章 开始", "第三章 后来"]
    assert book[starts[0][0]:].startswith("第一卷 风起\n第一章")
    assert book[starts[1][0]:].startswith("第二章 空\n第三章")


def test_best_decoding_keeps_gbk_full_of_decoration_symbols():
    data = ("★☆★第一章★☆★\n①他说②她说③※※■■□□\n" * 3 + "他。").encode("gbk")
    assert novel_text.best_decoding(data, ("gb18030", "big5"))[1] == "gb18030"


def test_epub_text_drops_ruby_readings_even_when_their_end_tag_is_left_out():
    from services.novel_attach_service import _html_to_text
    raw = "<p>他<ruby>漢<rt>かん</ruby>字。后面的正文。</p><p>第二段。</p>".encode()
    assert _html_to_text(raw) == "他漢字。后面的正文。\n第二段。"


def test_epub_contents_page_is_dropped_but_a_page_with_some_links_is_kept():
    from services.novel_attach_service import _html_to_text
    toc = "".join(f'<p><a href="c{i}.xhtml">第{i}章</a></p>' for i in range(1, 4))
    assert _html_to_text(f"<h1>目录</h1>{toc}".encode()) == ""
    notes = ('<h2><a href="c1.xhtml">后记</a></h2><p><a href="a">注一</a> 作者的话写在这里，'
             '比链接长。</p><p><a href="b">注二</a></p>')
    assert "作者的话" in _html_to_text(notes.encode())


def test_best_decoding_reads_big5_that_gb18030_also_decodes():
    data = "「今天要去山裡嗎？」隔壁的王嬸探出頭來問。".encode("big5")
    assert novel_text.best_decoding(data, ("gb18030", "big5")) == \
        ("「今天要去山裡嗎？」隔壁的王嬸探出頭來問。", "big5")
    gbk = "隔壁的王婶探出头来问。".encode("gbk")
    assert novel_text.best_decoding(gbk, ("gb18030", "big5"))[1] == "gb18030"
