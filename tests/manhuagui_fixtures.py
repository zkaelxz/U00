"""
tests/manhuagui_fixtures.py -- offline stand-ins for manhuagui pages,
built in the same shapes the real site serves (per Keiyoushi's
extension selectors) so the adapter can be tested with no network.

The chapter page is built the way the site builds it: the image-data
call is packed with Dean Edwards' p.a.c.k.e.r (base 62, ASCII word
tokens), and the packer's word list is LZString-compressed to Base64
and unpacked in-page via `'<b64>'['\\x73\\x70\\x6c\\x69\\x63']('\\x7c')`.
tests/test_sources_manhuagui.py also runs one of these pages through the
page's real JavaScript unpacker (in Node, when available) to confirm the
fixture itself is faithful, not just self-consistent.
"""

import json
import re

from sources.lzstring import compress_to_base64

_B62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

# The page's own unpacker, verbatim in shape (modern-browser branch).
PACKER_FN = ("function(p,a,c,k,e,d){e=function(c){return(c<a?\"\":e(parseInt(c/a)))+"
             "((c=c%a)>35?String.fromCharCode(c+29):c.toString(36))};if(!''.replace(/^/,String))"
             "{while(c--)d[e(c)]=k[c]||e(c);k=[function(e){return d[e]}];e=function(){return'\\\\w+'};"
             "c=1;};while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c]);return p;}")


def _b62(n: int) -> str:
    if n < 62:
        return _B62[n]
    return _b62(n // 62) + _B62[n % 62]


def pack(source: str) -> tuple:
    """Returns (payload, word_count, words_joined) like the real packer:
    ASCII \\w+ words ranked by frequency, each replaced by its base-62
    index; a word that already equals its own key is left blank in the
    list (the unpacker falls back to the key)."""
    tokens = re.findall(r"\w+", source, re.ASCII)
    order = []
    counts = {}
    for t in tokens:
        if t not in counts:
            order.append(t)
        counts[t] = counts.get(t, 0) + 1
    ranked = sorted(order, key=lambda w: (-counts[w], order.index(w)))
    keys = {w: _b62(i) for i, w in enumerate(ranked)}
    payload = re.sub(r"\w+", lambda m: keys[m.group(0)], source, flags=re.ASCII)
    words = ["" if keys[w] == w else w for w in ranked]
    return payload, len(ranked), "|".join(words)


def image_data(files, path, e=1790000000, m="AbC-dEf_9", cname="第01话"):
    return {"bid": 17332, "bname": "测试漫画", "bpic": "17332.jpg", "cid": 244506,
            "cname": cname, "files": files, "finished": False, "len": len(files),
            "path": path, "status": 1, "block_cc": "", "nextId": 244507, "prevId": 0,
            "sl": {"e": e, "m": m}}


def chapter_page(data: dict) -> str:
    call = "SMH.imgData(" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ").preInit();"
    payload, count, words = pack(call)
    b64 = compress_to_base64(words)
    script = (f'window["\\x65\\x76\\x61\\x6c"]({PACKER_FN}(\'{payload}\',62,{count},'
              f'\'{b64}\'[\'\\x73\\x70\\x6c\\x69\\x63\'](\'\\x7c\'),0,{{}}))')
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>测试漫画 第01话</title>"
            "<script src=\"https://cf.hamreus.com/scripts/core_E95E8E2C8C5C8CB4.js\"></script>"
            "</head><body><div id=\"mangaBox\"></div>"
            f"<script type=\"text/javascript\">{script}</script></body></html>")


SEARCH_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>搜索</title></head><body>
<div class="book-result"><ul>
 <li class="cf">
  <div class="book-cover fl"><a class="bcover" href="/comic/17332/" title="测试漫画">
   <img src="//cf.hamreus.com/cpic/h/17332.jpg" alt="测试漫画"></a></div>
  <div class="book-detail"><dl><dt><a href="/comic/17332/" title="测试漫画">测试<em>漫画</em></a>
   <small>百合</small></dt><dd class="tags status"><span>连载中</span></dd></dl></div>
 </li>
 <li class="cf">
  <div class="book-cover fl"><a class="bcover" href="/comic/9001/" title="另一部 测试">
   <img data-src="//cf.hamreus.com/cpic/h/9001.jpg"></a></div>
  <div class="book-detail"><dl><dt><a href="/comic/9001/" title="另一部 测试">另一部 测试</a></dt></dl></div>
 </li>
</ul></div>
<div class="pager-cont"><span class="current">1</span></div>
</body></html>"""

EMPTY_SEARCH_PAGE = """<html><body><div class="book-result"><ul></ul>
<div class="result-none">没有找到</div></div></body></html>"""

_CHAPTER_LIST_HTML = """
<h4><span>单话</span></h4>
<div class="chapter-list cf mt10" id="chapter-list-0">
 <ul style="display:block">
  <li><a href="/comic/17332/244508.html" title="第03话" class="status0"><span>第03话<i>18p</i></span></a></li>
  <li><a href="/comic/17332/244507.html" title="第02话" class="status0"><span>第02话<i>20p</i></span></a></li>
 </ul>
 <ul style="display:block">
  <li><a href="/comic/17332/244506.html" title="第01话" class="status0"><span>第01话<i>22p</i></span></a></li>
 </ul>
</div>
<h4><span>番外篇</span></h4>
<div class="chapter-list cf mt10" id="chapter-list-1">
 <ul><li><a href="/comic/17332/250001.html" class="status0"><span>番外 夏日<i>8p</i></span></a></li></ul>
</div>"""

_SERIES_HEAD = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>测试漫画</title></head><body>
<div class="book-cont cf"><div class="book-cover fl"><p class="hcover">
 <img src="//cf.hamreus.com/cpic/b/17332.jpg" alt="测试漫画"></p></div>
<div class="book-detail pr fr">
 <div class="book-title"><h1>测试漫画</h1><h2>Test Comic</h2></div>
 <ul class="detail-list cf">
  <li><span><strong>出品年代：</strong><a href="/list/2024/">2024年</a></span></li>
  <li><span><strong>漫画剧情：</strong><a href="/list/baihe/">百合</a><a href="/list/aiqing/">爱情</a></span>
      <span><strong>漫画作者：</strong><a href="/author/1/">作者甲</a></span></li>
  <li class="status"><span><strong>漫画状态：</strong><span class="red">连载中</span>。
      最近于 [<span class="red">2026-09-01</span>] 更新至 [ <a href="/comic/17332/244508.html" class="blue">第03话</a> ]。</span></li>
 </ul>
 <div id="intro-all"><p>两个女孩的故事。</p></div>
</div></div>
<div class="chapter cf mt16">"""

SERIES_PAGE = _SERIES_HEAD + _CHAPTER_LIST_HTML + "</div></body></html>"

SERIES_PAGE_ADULT = (_SERIES_HEAD
                     + '<div id="erroraudit_show">此漫画已被列为限制级</div>'
                     + '<input type="hidden" id="__VIEWSTATE" value="'
                     + compress_to_base64(_CHAPTER_LIST_HTML) + '"/>'
                     + "</div></body></html>")

CHANGED_LAYOUT_PAGE = "<html><body><div class='new-design'>全新改版</div></body></html>"
