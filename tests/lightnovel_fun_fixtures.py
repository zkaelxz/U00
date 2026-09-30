"""
tests/lightnovel_fun_fixtures.py -- recorded-shape pages for the 轻之国度
(lightnovel.fun) adapter tests.

Recorded from the live site on 2026-09-30 (/book/33139, /reader/33139/
323701, /reader/33139/323778, /search?keyword=百合), then trimmed: comments,
user records and long chapter bodies are cut, and the fields the adapter
reads are kept with their real values. `encode()` rebuilds Nuxt's devalue
array the way the real pages carry it (a flat list whose objects point at
other entries by index, wrapped in ["ShallowReactive", n]).
"""

import json


def encode(value) -> str:
    out = []

    def add(v):
        idx = len(out)
        out.append(None)
        if isinstance(v, dict):
            out[idx] = {k: add(x) for k, x in v.items()}
        elif isinstance(v, list):
            out[idx] = [add(x) for x in v]
        else:
            out[idx] = v
        return idx

    out.append(["ShallowReactive", 1])
    add({"data": None, "state": {}, "serverRendered": True})
    out[1]["data"] = len(out)
    out.append(["ShallowReactive", len(out) + 1])
    add(value)
    return json.dumps(out, ensure_ascii=False)


def page(data: dict, body: str = "") -> str:
    return ("<!DOCTYPE html><html lang=\"zh-Hans\"><head><meta charset=\"utf-8\">"
            "<title>轻之国度-专注分享的NACG社群</title></head><body><div id=\"__nuxt\">"
            f"<main class=\"site-main\">{body}</main></div>"
            "<script type=\"application/json\" data-nuxt-data=\"nuxt-app\" data-ssr=\"true\" "
            f"id=\"__NUXT_DATA__\">{encode(data)}</script></body></html>")


def _ch(cid, title, order, locked=False, access="public", price=0):
    return {"id": cid, "title": title, "order": order, "updatedAt": "2026-09-20 07:51:50",
            "locked": locked, "accessType": access, "unlocked": not locked, "coinPrice": price,
            "finished": None, "readPercent": None}


BOOK = {
    "id": "33139", "bookId": "33139",
    "title": "只有颜值是优点的同学，以猛烈攻势向我扑来的百合故事。",
    "cover": "https://api.lightnovel.fun/upload-files/images/260910/371429dc59497af44c4a9729112d6fd5.jpg",
    "subtitle": None, "author": "能代リョウ", "posterName": "沐梓", "illustrator": "べにしゃけ",
    "translator": None,
    "summary": "她的优点可不止“颜值”！？（※先动心的人是我）\n\n我是只想度过平凡高中生活的——海道律。",
    "tags": ["百合", "恋爱", "校园", "青春"], "status": "连载中", "chapterCount": 13,
    "updatedAt": "2026-09-24 13:01:25", "epubReady": False, "targetType": "book", "volumes": [],
}

VOLUME_1 = [
    _ch("323383", "制作信息", 1), _ch("323384", "彩页", 2),
    _ch("323385", "第一话 我才不会输给矢来同学呢。", 3), _ch("323652", "第二话 我就不行吗？", 4),
    _ch("323653", "第三话 海道同学，你喜欢这样吗？", 5), _ch("323697", "第四话 请和我约会吧。", 6),
    _ch("323698", "第五话 矢来同学，我喜欢你。", 7), _ch("323699", "后记", 8),
    _ch("323700", "番外篇", 9), _ch("323701", "Bookwalker特典", 10),
]
VOLUME_WEB = [_ch("323778", "制作信息", 1), _ch("323779", "第41话", 2), _ch("323829", "第42话", 3)]


def catalog(first_loaded=True, web_loaded=False):
    return [
        {"id": "50701", "title": "第一卷", "cover": None,
         "chapters": VOLUME_1 if first_loaded else [], "chapterCount": 10,
         "chaptersLoaded": first_loaded},
        {"id": "50789", "title": "web版", "cover": None,
         "chapters": VOLUME_WEB if web_loaded else [], "chapterCount": 3,
         "chaptersLoaded": web_loaded},
    ]


# The real book page renders only 8 of volume 1's chapters in the grid.
BOOK_GRID = ("<section class=\"chapter-box\"><div class=\"chapter-grid\">"
             + "".join(f"<a class=\"chapter\" href=\"/reader/33139/{c['id']}\"><span>{c['title']}</span></a>"
                       for c in VOLUME_1[:8])
             + "</div></section>")

BOOK_PAGE = page({"pc-auth-state": {"loggedIn": False, "user": None},
                  "pc-book-detail-33139": {"book": BOOK, "catalog": catalog(),
                                           "catalogComplete": True}},
                 f"<section class=\"detail-layout\"><h1>{BOOK['title']}</h1></section>{BOOK_GRID}")


def reader_chapter(cid, title, volume_id, prev_id, next_id, content_html,
                   locked=False, access="public", price=0):
    return {"bookId": "33139", "chapterId": cid, "locked": locked, "accessType": access,
            "coinPrice": price, "volumeId": volume_id, "title": title, "subtitle": None,
            "contentHtml": content_html, "prevChapterId": prev_id, "nextChapterId": next_id,
            "volumeTitle": "第一卷" if volume_id == "50701" else "web版", "legacyResource": None}


def reader_page(cid, current, cat):
    body = ""
    if current.get("contentHtml"):
        body = f"<div class=\"reader-body\"><div class=\"reader-text\">{current['contentHtml']}</div></div>"
    return page({"pc-auth-state": {"loggedIn": False, "user": None},
                 f"reader-bootstrap-33139-{cid}-public": {
                     "book": BOOK, "currentChapter": current, "catalog": cat,
                     "catalogComplete": True, "catalogError": False}}, body)


CHAPTER_323385_HTML = (
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">昨天，也就是暑假最后一天，我把头发剪了。</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">一直留到腰间的黑发，被我一口气剪掉了。</p>\n"
    "<p class=\"ln-paragraph\"><br /></p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">难以理解。</p>")

# Real 制作信息 chapter body of 第一卷 (/reader/33139/323383), trimmed: it
# posts the translator's own EPUB on Lanzou, with the password on the next line.
CHAPTER_323383_HTML = (
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">只有颜值是优点的同学，以猛烈攻势向我扑来的百合故事。</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">富士见Fantasia文库 出品</p>\n<hr />\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">翻译：沐梓</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">校对、Epub：云淡</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">Epub获取：<a href=\"https://wwasa.lanzoue.com/b0188mxnyb\" "
    "target=\"_blank\" rel=\"noopener noreferrer\">https://wwasa.lanzoue.com/b0188mxnyb</a></p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">密码:be3j</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">仅供个人学习交流使用，禁作商业用途。</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">下载后请在24小时内删除，LK不负担任何责任。</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">请尊重翻译、校对的辛勤劳动。</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">如需转载请保留制作信息（及群号）。</p>\n<hr />")

# Real 制作信息 chapter body (/reader/33139/323778), verbatim.
CHAPTER_323778_HTML = (
    "<p class=\"ln-paragraph\">原作链接：<a href=\"https://kakuyomu.jp/works/1177354054919288428\" "
    "target=\"_blank\" rel=\"noopener noreferrer\">https://kakuyomu.jp/works/1177354054919288428</a></p>\n"
    "<p class=\"ln-paragraph\">请多多支持原作！</p>\n<p class=\"ln-paragraph\"><br /></p>\n"
    "<p class=\"ln-paragraph\">作者：能代リョウ</p>\n<p class=\"ln-paragraph\">翻译：沐梓</p>\n"
    "<p class=\"ln-paragraph\">校对：云淡</p>\n<p class=\"ln-paragraph\">个翻作品交流群：1104800883</p>\n"
    "<p class=\"ln-paragraph\"><br /></p>\n<p class=\"ln-paragraph\">仅供个人学习交流使用，禁作商业用途。</p>\n"
    "<p class=\"ln-paragraph\">请尊重翻译、校对的辛勤劳动。</p>\n"
    "<p class=\"ln-paragraph\">如需转载请保留制作信息（及群号）。</p>\n<p class=\"ln-paragraph\"><br /></p>\n"
    "<p class=\"ln-paragraph\">文库版对应web1-40话，新合集从第41话开始翻译。</p>\n"
    "<p class=\"ln-paragraph\"><span style=\"color:#d14343\">原文已断更。</span>目前共有41-87话及两篇番外。</p>")

READER_323383 = reader_page("323383", reader_chapter(
    "323383", "制作信息", "50701", None, "323384", CHAPTER_323383_HTML), catalog())
READER_323385 = reader_page("323385", reader_chapter(
    "323385", "第一话 我才不会输给矢来同学呢。", "50701", "323384", "323652", CHAPTER_323385_HTML),
    catalog())
# The last chapter of volume 1: its nextChapterId is web版's first chapter,
# but its catalog still leaves web版 unloaded (as recorded).
READER_323701 = reader_page("323701", reader_chapter(
    "323701", "Bookwalker特典", "50701", "323700", "323778", "<p class=\"ln-paragraph\">特典。</p>"),
    catalog())
# A web版 chapter's reader page loads web版 and unloads volume 1 (as recorded).
READER_323778 = reader_page("323778", reader_chapter(
    "323778", "制作信息", "50789", "323701", "323779", CHAPTER_323778_HTML),
    catalog(first_loaded=False, web_loaded=True))

# Illustrations-only chapter (彩页): images and no text.
READER_323384 = reader_page("323384", reader_chapter(
    "323384", "彩页", "50701", "323383", "323385",
    "<p class=\"ln-paragraph\"><img class=\"reader-resource-image\" "
    "src=\"https://res.lightnovel.fun/a.jpg\" /></p>"), catalog())

# A real locked, paid chapter (/reader/31629/318181, recorded 2026-09-30):
# locked, accessType "coin", 20 轻币, and a teaser body whose resource slots
# read [资源解锁后可用]. Trimmed teaser, real values.
READER_LOCKED = page({
    "pc-auth-state": {"loggedIn": False, "user": None},
    "reader-bootstrap-33139-323829-public": {
        "book": BOOK, "catalog": catalog(first_loaded=False, web_loaded=True),
        "currentChapter": {
            "bookId": "33139", "chapterId": "323829", "locked": True, "accessType": "coin",
            "coinPrice": 20, "volumeId": "50789", "title": "第42话", "subtitle": None,
            "prevChapterId": "323779", "nextChapterId": None, "legacyResource": None,
            "contentHtml": (
                "<p><img src=\"%5B%E8%B5%84%E6%BA%90%E8%A7%A3%E9%94%81%E5%90%8E%E5%8F%AF%E7%94%A8%5D\" "
                "img-width=\"797\" img-height=\"1200\" style=\"max-width:100%;height:auto;\"></p>\n"
                "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">轻之国度×天使动漫录入组</p>\n"
                "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">轻之国度：[资源解锁后可用]</p>\n"
                "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">仅供个人学习交流使用，禁作商业用途</p>")}}},
    "<div class=\"reader-body\"><div class=\"reader-text\"><p>轻之国度×天使动漫录入组</p></div></div>")

SEARCH_ITEMS = [
    {"id": "993", "bookId": "993", "title": "将放言说不会输的高颜值女孩，全力征服的百合故事",
     "cover": "https://api.lightnovel.fun/upload-files/images/200705/aced34afee9e24cf17dad7714a37e8bc.jpg",
     "author": "みかみてれんx未幡（翻译：百合食谱研究社", "status": "连载中", "chapterCount": 8,
     "targetType": "book"},
    {"id": "1353", "bookId": "1353",
     "title": "转生为百合游戏的反派女后想回避坏结局，但却不知道为何和女主角好上了",
     "cover": "", "author": "Yuri", "status": "连载中", "chapterCount": 41, "targetType": "book"},
    {"id": "33139", "bookId": "33139", "title": BOOK["title"], "cover": BOOK["cover"],
     "author": "能代リョウ", "status": "连载中", "chapterCount": 13, "targetType": "book"},
]

SEARCH_KEY = ('{"keyword":"百合","page":1,"type":"all","primaryTag":"","workType":"","source":"",'
              '"wordCountBucket":"","statusBucket":"","sort":"relevance","preset":""}')

SEARCH_PAGE = page({
    "pc-auth-state": {"loggedIn": False, "user": None},
    "pc-search-taxonomy": {"channels": [{"id": "lightnovel", "label": "轻小说"}]},
    SEARCH_KEY: {"items": SEARCH_ITEMS, "page": 1, "pageSize": 20, "total": 317, "hasMore": True},
    # The side board lists other books; it isn't a search result.
    "pc-search-board-daily-all": [{"id": "1338", "bookId": "1338", "title": "败犬女主太多了！"}],
})

CLOUDFLARE_PAGE = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
                   "<div id=\"challenge-running\">Checking your browser before accessing "
                   "www.lightnovel.fun.</div><script src=\"/cdn-cgi/challenge-platform/h/b/orchestrate/"
                   "chl_page/v1\"></script></body></html>")


# An EPUB work (/book/642, /reader/642/262972, recorded 2026-09-30): its one
# public chapter is a resource post with a Baidu Pan link and 提取码.
BOOK_642 = {"id": "642", "bookId": "642", "title": "龙盘七朝 DRAGONBUSTER", "cover": "",
            "author": "秋山瑞人", "illustrator": None, "summary": "作者：秋山瑞人\n译者：桂渚浮槎",
            "tags": ["epub", "奇幻"], "status": "连载中", "chapterCount": 1, "epubReady": False,
            "targetType": "book", "volumes": []}
CATALOG_642 = [{"id": "24877", "title": "[01][日翻/简]", "cover": None,
                "chapters": [_ch("262972", "[01][日翻/简]", 1)], "chapterCount": 1,
                "chaptersLoaded": True}]
BOOK_642_PAGE = page({"pc-book-detail-642": {"book": BOOK_642, "catalog": CATALOG_642}})
CHAPTER_262972_HTML = (
    "<p><img src=\"https://api.lightnovel.fun/upload-files/images/250223/497306e4f03e49b7f191007527e694a2.png\" /></p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">【翻译信息】</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">翻译：桂渚浮槎</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">轻之国度：https://www.lightnovel.fun</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">仅供个人学习交流使用，禁作商业用途。</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">下载后请在24小时内删除，LK不负担任何责任。</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">转发时请保留本帖所有信息。</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">【资源下载】</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">百度网盘</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">链接：https://pan.baidu.com/s/1UW8fzsl6WfJ1RRIXRt_MPw?pwd=roh1</p>\n"
    "<p class=\"ln-paragraph ln-paragraph--indent\" style=\"text-indent:2em;\">提取码：roh1</p>")
READER_262972 = page({"reader-bootstrap-642-262972-public": {
    "book": BOOK_642, "catalog": CATALOG_642,
    "currentChapter": {"bookId": "642", "chapterId": "262972", "locked": False,
                       "accessType": "public", "coinPrice": 0, "volumeId": "24877",
                       "title": "[01][日翻/简]", "contentHtml": CHAPTER_262972_HTML,
                       "prevChapterId": None, "nextChapterId": None}}})
