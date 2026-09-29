"""
tests/test_adaptive_extraction.py -- Step 23g: deterministic-first content
extraction with an LLM fallback, independent confidence checks, versioned
per-domain profiles, the page-hash result cache and the diagnostics log.

No network and no real model: the "LLM" is a fake engine behind a
monkeypatched translate_engines.call_llm_json that answers with block/
link/candidate ids (looked up from the same deterministic page model the
code under test builds) and counts its calls.
"""

import json

import pytest

from sources import adaptive, ai_extract as ax, generic_import, profiles, store

from .sources_helpers import ScriptedTransport, html as html_resp, make_client

URL = "https://www.novel.example/book/77/1002.html"
DOMAIN = "novel.example"

_SENTENCES = ["她沿着河岸慢慢走着，想起了很多年前的那个夏天。", "雨一直下，街灯在水洼里碎成一片一片。",
              "他没有回头，只是把伞往她那边偏了偏。", "远处传来钟声，一下一下，像是在数着什么。",
              "风从巷子口灌进来，带着潮湿的桂花香。", "她忽然笑了，说自己其实什么都不记得了。",
              "门口的石阶上积了一层薄薄的青苔。", "那封信她一直没有拆，就放在抽屉最里面。"]


def chapter_html(n=12, container_id="content", title_wrap="bookname", extra_links="",
                 prev=True):
    paras = "".join(f"<p>第{n}章第{i}段。{_SENTENCES[i % 8]}{_SENTENCES[(i * 3 + n) % 8]}</p>"
                    for i in range(10))
    prev_link = f'<a id="prev" href="/book/77/{1000 + n - 1}.html">上一章</a>' if prev else ""
    return f"""<html><head><title>第{n}章 重逢 - 某某小说网</title></head><body>
<div class="nav"><a href="/">首页</a><a href="/book/77/">目录</a><a href="/top">排行</a></div>
<div id="{title_wrap}"><h1>第{n}章 重逢</h1><span class="author">作者：青山</span></div>
<div id="{container_id}">{paras}<p>“你来了。”她说。</p><h3>二</h3>{paras.replace('段。', '节。')}
<div class="ad">广告：点击下载APP，免费看全本</div></div>
<div class="page">{prev_link}<a href="/book/77/">目录</a>
<a id="next" href="/book/77/{1000 + n + 1}.html">下一章</a>{extra_links}</div>
</body></html>"""


def chapter_url(n):
    return f"https://www.novel.example/book/77/{1000 + n}.html"


class FakeEngine:
    """Stands in for a real LLM engine. `answer(prompt)` returns a dict
    (sent back as JSON text) or None."""
    supports_reference = True
    model = "fake-model-1"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []


@pytest.fixture
def fake_llm(monkeypatch):
    import translate_engines

    def fake_call(engine, prompt, max_tokens=2000, fallback="[]", usage_cb=None):
        engine.calls.append(prompt)
        out = engine.answer(prompt)
        return fallback if out is None else json.dumps(out, ensure_ascii=False)
    monkeypatch.setattr(translate_engines, "call_llm_json", fake_call)
    return fake_call


def _block(page, text, tag=None):
    return next(b.id for b in page.blocks if b.text == text and (tag is None or b.tag == tag))


def _link(page, text):
    return next(l.id for l in page.links if l.text == text)


def novel_answer(prev=True, conf=None, next_text="下一章"):
    """A well-behaved model: body = the content container, ad excluded,
    title/heading/dialogue marked, and previous/author left null when
    asked to (the model 'couldn't find' them)."""
    def answer(prompt):
        url = prompt.split("URL: ", 1)[1].split("\n", 1)[0]
        page = ax.PageModel(_HTML_BY_URL[url], url)
        body = [b for b in page.blocks if b.tag in ("p", "h3")]
        ad = next(b.id for b in page.blocks if b.text.startswith("广告"))
        return {"title_block": None, "author_block": None,
                "chapter_title_block": next(b.id for b in page.blocks if b.tag == "h1"),
                "chapter_number": "12" if "1002" in url else None,
                "body_range": [body[0].id, ad], "exclude_blocks": [ad],
                "heading_blocks": [next(b.id for b in page.blocks if b.tag == "h3")],
                "dialogue_blocks": [_block(page, "“你来了。”她说。")],
                "next_link": _link(page, next_text),
                "previous_link": _link(page, "上一章") if prev else None,
                "toc_links": [_link(page, "目录")],
                "confidence": conf or {"content": 0.95, "chapter_title": 0.9}}
    return answer


_HTML_BY_URL = {}


def _page(n=12, **kw):
    u = chapter_url(n)
    h = chapter_html(n, **kw)
    _HTML_BY_URL[u] = h
    return h, u


@pytest.fixture
def no_deterministic(monkeypatch):
    """The deterministic tier comes back empty (mocked)."""
    monkeypatch.setattr(generic_import, "extract_main_text", lambda html, url="": ("", "heuristic"))


# ---------------------------------------------------------------------------
# Exit 1 -- the LLM runs only when the deterministic pass is ambiguous/empty
# ---------------------------------------------------------------------------

class TestLlmOnlyAsFallback:
    def test_deterministic_success_makes_no_llm_call(self, isolated_db, fake_llm):
        h, u = _page(12)
        engine = FakeEngine(novel_answer())
        data, report = adaptive.extract_novel(h, u, engine)
        assert data["valid"] and report.extraction_tier == "deterministic"
        assert engine.calls == [] and report.llm_calls == 0

    def test_mocked_success_vs_mocked_empty(self, isolated_db, fake_llm, monkeypatch):
        h, u = _page(12)
        good = "\n".join(p for p in ax.PageModel(h, u).text.splitlines() if "段。" in p)
        monkeypatch.setattr(generic_import, "extract_main_text", lambda html, url="": (good, "heuristic"))
        engine = FakeEngine(novel_answer())
        _data, report = adaptive.extract_novel(h, u, engine)
        assert engine.calls == [] and report.extraction_tier == "deterministic"

        monkeypatch.setattr(generic_import, "extract_main_text", lambda html, url="": ("", "heuristic"))
        _data, report = adaptive.extract_novel(h, u, engine, use_cache=False)
        assert len(engine.calls) == 1 and report.llm_calls == 1
        assert report.extraction_tier == "llm"

    def test_ambiguous_deterministic_result_triggers_exactly_one_call(self, isolated_db, fake_llm,
                                                                    monkeypatch):
        h, u = _page(12)
        # Deterministic tier picks the nav bar's text 30 times over: long
        # enough, but obviously not prose.
        monkeypatch.setattr(generic_import, "extract_main_text",
                            lambda html, url="": ("\n".join(["首页 目录 排行"] * 30), "heuristic"))
        engine = FakeEngine(novel_answer())
        data, report = adaptive.extract_novel(h, u, engine)
        assert len(engine.calls) == 1 and report.extraction_tier == "llm"
        assert "ambiguous" in " ".join(report.lines)

    def test_no_engine_means_no_call_and_an_honest_reason(self, isolated_db, no_deterministic):
        h, u = _page(12)
        data, report = adaptive.extract_novel(h, u, engine=None)
        assert data is None and report.llm_calls == 0
        assert "No AI engine is set up" in " ".join(report.lines)
        assert "Set up an AI engine" in report.reason

    def test_comic_filter_success_makes_no_llm_call(self, isolated_db, fake_llm):
        from .sources_helpers import image
        u = "https://comic.example/read/5"
        page_html = "".join(f'<img src="https://comic.example/p/{i}.png">' for i in range(4))
        client = make_client("generic", ScriptedTransport({
            u: html_resp(page_html),
            **{f"https://comic.example/p/{i}.png": image(800, 1200, i) for i in range(4)}}))
        engine = FakeEngine(lambda p: pytest.fail("the LLM must not be asked"))
        res, report = adaptive.import_comic(u, engine, client=client)
        assert len(res.images) == 4 and report.extraction_tier == "deterministic"
        assert engine.calls == [] and report.llm_calls == 0


# ---------------------------------------------------------------------------
# Exit 2 -- novel result: per-field confidence, boundaries kept, null not guess
# ---------------------------------------------------------------------------

class TestNovelExtraction:
    def test_confidence_per_field_boundaries_and_nulls(self, isolated_db, fake_llm, no_deterministic):
        h, u = _page(1, prev=False)
        engine = FakeEngine(novel_answer(prev=False))
        data, report = adaptive.extract_novel(h, u, engine)
        assert report.extraction_tier == "llm" and data["valid"]

        for f in ("title", "author", "chapter_title", "chapter_number", "content",
                  "next_url", "previous_url"):
            c = data["confidence"][f]
            assert isinstance(c["score"], float) and c["bucket"] in ("HIGH", "MEDIUM", "LOW", "FAILED")
        assert data["confidence"]["content"]["bucket"] == "HIGH"

        # Paragraph and heading boundaries come straight from the page.
        page = ax.PageModel(h, u)
        want = [b.text for b in page.blocks if b.tag in ("p", "h3")]
        assert [p["text"] for p in data["paragraphs"]] == want
        assert data["content"] == "\n".join(want)
        kinds = {p["text"]: p["kind"] for p in data["paragraphs"]}
        assert kinds["二"] == "heading"
        assert [p["text"] for p in data["paragraphs"] if p["dialogue"]] == ["“你来了。”她说。"]
        assert not any("广告" in p["text"] for p in data["paragraphs"])
        assert data["chapter_title"] == "第1章 重逢"
        assert data["next_url"] == chapter_url(2)

        # Things the model couldn't find are null -- not a guess.
        assert data["previous_url"] is None and data["author"] is None and data["title"] is None
        assert data["confidence"]["previous_url"]["bucket"] == "FAILED"
        assert data["confidence"]["author"]["bucket"] == "FAILED"
        assert data["chapter_number"] is None     # the model said null; nothing was filled in

    def test_model_cannot_rewrite_text(self, isolated_db, fake_llm, no_deterministic):
        """Even a model that tries to hand back text gets only ids read:
        the content is copied from the page."""
        h, u = _page(12)

        def answer(prompt):
            a = novel_answer()(prompt)
            a["body"] = "A made-up English summary of the chapter."
            return a
        data, _ = adaptive.extract_novel(h, u, FakeEngine(answer))
        assert "summary" not in data["content"] and data["content"].startswith("第12章第0段。")

    def test_unknown_ids_are_ignored_not_matched_by_position(self, isolated_db):
        h, u = _page(12)
        page = ax.PageModel(h, u)
        data = ax.novel_from_picks(page, {"body_blocks": ["b999", "nonsense"], "next_link": "L999"})
        assert data["paragraphs"] == [] and data["next_url"] is None


# ---------------------------------------------------------------------------
# Exit 3 -- comic classification separates pages from chrome, downloads nothing
# ---------------------------------------------------------------------------

COMIC_URL = "https://comic.example/read/77/5"
COMIC_HTML = """<html><head><title>第5话 雨夜 - 漫画站</title></head><body><h1>第5话 雨夜</h1>
<div class="cover"><img src="https://comic.example/covers/77.jpg" alt="封面" width="300" height="400"></div>
<div id="reader">
  <img data-src="https://img.cdn.example/comic/77/5/001.jpg" src="data:image/gif;base64,R0lGOD">
  <picture><source srcset="https://img.cdn.example/comic/77/5/002_small.jpg 400w, https://img.cdn.example/comic/77/5/002.jpg 1200w">
    <img src="https://img.cdn.example/comic/77/5/002_small.jpg"></picture>
  <div class="ad-slot"><a href="https://ads.example.net/c"><img src="https://ads.example.net/banner/9.jpg"></a></div>
  <img src="https://img.cdn.example/comic/77/5/thumbs/001.jpg" width="100" height="140">
</div>
<div class="recommend"><a href="/book/88/"><img src="https://comic.example/covers/88.jpg"></a></div>
<script>var pages = ["https:\\/\\/img.cdn.example\\/comic\\/77\\/5\\/003.jpg"];</script>
</body></html>"""


def comic_answer(prompt):
    """Mostly right, with two mistakes the independent pass must catch: it
    calls the small srcset variant and the thumbnail 'content' too."""
    lines = [json.loads(l) for l in prompt.split("IMAGES:\n", 1)[1].splitlines() if l.strip()]
    role = {"covers/77": ("cover", None), "001.jpg": ("content", 1), "002.jpg": ("content", 2),
            "002_small": ("content", 2), "ads.example": ("ad", None), "thumbs/001": ("content", 1),
            "covers/88": ("recommendation", None), "003.jpg": ("content", 3)}
    out = []
    for d in lines:
        key = next(k for k in role if k in d["url"] and not (k == "001.jpg" and "thumbs" in d["url"]))
        r, pg = role[key]
        out.append({"id": d["id"], "role": r, "page": pg, "confidence": 0.9})
    return {"title": None, "chapter_title": "第5话 雨夜", "chapter_number": "5", "images": out}


class TestComicClassification:
    def test_separates_content_from_chrome_without_downloading(self, isolated_db, fake_llm,
                                                               monkeypatch):
        def no_download(*a, **k):
            raise AssertionError("classification must not download anything")
        monkeypatch.setattr(generic_import, "download_candidates", no_download)
        import requests
        monkeypatch.setattr(requests, "get", no_download)
        engine = FakeEngine(comic_answer)

        data, report = adaptive.classify_comic_page(COMIC_HTML, COMIC_URL, engine)
        assert len(engine.calls) == 1 and report.llm_calls == 1
        assert data["content_type"] == "comic"
        assert set(data) >= {"title", "chapter_title", "chapter_number", "pages"}
        content = [p for p in data["pages"] if p["role"] == "content"]
        assert [p["resource_url"].rsplit("/", 1)[-1] for p in content] == ["001.jpg", "002.jpg", "003.jpg"]
        assert [p["index"] for p in content] == [0, 1, 2]
        attrs = {p["resource_url"].rsplit("/", 1)[-1]: p["source_attr"] for p in content}
        assert attrs == {"001.jpg": "data-src", "002.jpg": "srcset", "003.jpg": "manifest"}
        roles = {p["resource_url"]: p["role"] for p in data["pages"]}
        assert roles["https://comic.example/covers/77.jpg"] == "cover"
        assert roles["https://ads.example.net/banner/9.jpg"] == "ad"
        assert roles["https://comic.example/covers/88.jpg"] == "recommendation"
        assert roles["https://img.cdn.example/comic/77/5/002_small.jpg"] == "duplicate"
        assert roles["https://img.cdn.example/comic/77/5/thumbs/001.jpg"] in ("duplicate", "thumbnail")
        for p in data["pages"]:
            assert set(p) >= {"index", "resource_url", "role", "confidence"}
        assert data["chapter_number"] == "5" and data["confidence"]["page_order"]["bucket"] == "HIGH"

    def test_script_listed_urls_are_fetched_only_when_tags_find_nothing(self, isolated_db):
        from .sources_helpers import image
        u = "https://comic.example/read/3"
        covers = "".join(f'"https://comic.example/covers/{i}.jpg",' for i in range(5))
        tags = "".join(f'<img src="https://comic.example/p/{i}.png">' for i in range(3))
        routes = {u: html_resp(tags + f"<script>var rec=[{covers}];</script>")}
        routes.update({f"https://comic.example/p/{i}.png": image(800, 1200, i) for i in range(3)})
        transport = ScriptedTransport(routes)
        res, report = adaptive.import_comic(u, None, client=make_client("generic", transport))
        assert len(res.images) == 3 and report.extraction_tier == "deterministic"
        assert not any("/covers/" in c for c in transport.urls())

        u2 = "https://comic.example/read/4"
        pages = "".join(f'"https:\\/\\/comic.example\\/q\\/{i}.png",' for i in range(3))
        routes = {u2: html_resp(f"<div id=reader></div><script>var pages=[{pages}];</script>")}
        routes.update({f"https://comic.example/q/{i}.png": image(800, 1200, 40 + i) for i in range(3)})
        res2, report2 = adaptive.import_comic(u2, None, client=make_client("generic", ScriptedTransport(routes)))
        assert [c.url.rsplit("/", 1)[-1] for c in res2.images] == ["0.png", "1.png", "2.png"]
        assert report2.extraction_tier == "deterministic" and report2.llm_calls == 0

    def test_import_hands_only_llm_pages_to_the_existing_downloader(self, isolated_db, fake_llm):
        """End to end: the filter keeps the cover and ad (page-sized, same
        CDN) -- ambiguous -- so the model is asked once, and the existing
        downloader's images are what gets imported."""
        from .sources_helpers import image
        u = "https://comic.example/read/9"
        page_html = ('<div class="cover"><img src="https://comic.example/p/cover.png" alt="cover"></div>'
                     + "".join(f'<img src="https://comic.example/p/{i}.png">' for i in range(1, 4))
                     + '<div class="ad-box"><img src="https://comic.example/p/promo.png"></div>')
        routes = {u: html_resp(page_html),
                  "https://comic.example/p/cover.png": image(800, 1200, 9),
                  "https://comic.example/p/promo.png": image(800, 1200, 8)}
        routes.update({f"https://comic.example/p/{i}.png": image(800, 1200, i) for i in range(1, 4)})
        transport = ScriptedTransport(routes)

        def answer(prompt):
            lines = [json.loads(l) for l in prompt.split("IMAGES:\n", 1)[1].splitlines() if l.strip()]
            return {"images": [{"id": d["id"],
                                "role": "cover" if "cover" in d["url"] else
                                        "ad" if "promo" in d["url"] else "content",
                                "page": None} for d in lines]}
        engine = FakeEngine(answer)
        res, report = adaptive.import_comic(u, engine, client=make_client("generic", transport))
        assert report.extraction_tier == "llm" and len(engine.calls) == 1
        assert [c.url.rsplit("/", 1)[-1] for c in res.images] == ["1.png", "2.png", "3.png"]
        assert all(c.content for c in res.images)
        assert {c.url.rsplit("/", 1)[-1] for c in res.rejected} == {"cover.png", "promo.png"}


# ---------------------------------------------------------------------------
# Exit 4 -- media resources on a page the video adapter doesn't know
# ---------------------------------------------------------------------------

VIDEO_URL = "https://www.unknown-video.example/watch/ep3"
VIDEO_HTML = """<html><head><title>第3集 - 某视频站</title></head><body><h1>第3集</h1>
<video id="player" poster="/p.jpg">
  <source src="https://media.video.example/v/ep3/master.m3u8" type="application/x-mpegURL">
  <track kind="subtitles" src="/subs/ep3.zh.vtt" srclang="zh" label="中文">
</video>
<script>var cfg = {"preroll": "https://adserver.example/pre/roll.mp4",
 "next_trailer": "https:\\/\\/media.video.example\\/v\\/ep4\\/trailer.mp4"};</script>
</body></html>"""


def media_answer(prompt):
    lines = [json.loads(l) for l in prompt.split("RESOURCES:\n", 1)[1].splitlines() if l.strip()]
    role = {"master.m3u8": "main", "ep3.zh.vtt": "subtitle", "roll.mp4": "ad", "trailer.mp4": "trailer"}
    return {"title": "第3集",
            "resources": [{"id": d["id"], "role": next(r for k, r in role.items() if k in d["url"]),
                           "language": "zh" if "vtt" in d["url"] else None, "confidence": 0.9}
                          for d in lines]}


class TestMediaResources:
    def test_identifies_media_and_subtitles_without_downloading(self, isolated_db, fake_llm,
                                                                 monkeypatch):
        import requests
        import video_download

        def no_download(*a, **k):
            raise AssertionError("identification must not download anything")
        monkeypatch.setattr(video_download, "download", no_download)
        monkeypatch.setattr(requests, "get", no_download)
        from sources import front_door, registry
        assert registry.find_for_url(VIDEO_URL) is None and not front_door.is_video_url(VIDEO_URL)

        engine = FakeEngine(media_answer)
        data, report = adaptive.identify_media(VIDEO_URL, VIDEO_HTML, engine)
        assert report.extraction_tier == "llm" and len(engine.calls) == 1
        by_name = {r["resource_url"].rsplit("/", 1)[-1]: r for r in data["resources"]}
        assert by_name["master.m3u8"]["role"] == "main" and by_name["master.m3u8"]["kind"] == "manifest"
        assert by_name["ep3.zh.vtt"]["role"] == "subtitle" and by_name["ep3.zh.vtt"]["language"] == "zh"
        assert by_name["ep3.zh.vtt"]["resource_url"] == "https://www.unknown-video.example/subs/ep3.zh.vtt"
        assert by_name["roll.mp4"]["role"] == "ad" and by_name["trailer.mp4"]["role"] == "trailer"
        assert data["valid"] and data["title"] == "第3集"

    def test_urls_the_existing_video_path_handles_are_left_alone(self, isolated_db, fake_llm):
        engine = FakeEngine(media_answer)
        for u in ("https://www.bilibili.com/video/BV1xx411c7mD", "https://www.youtube.com/watch?v=x"):
            data, report = adaptive.identify_media(u, VIDEO_HTML, engine)
            assert data is None
        assert engine.calls == []

    def test_one_clear_resource_needs_no_llm(self, isolated_db, fake_llm):
        engine = FakeEngine(media_answer)
        page = '<video src="https://media.video.example/v/1.mp4"></video>'
        data, report = adaptive.identify_media(VIDEO_URL, page, engine)
        assert report.extraction_tier == "deterministic" and engine.calls == []
        assert data["resources"][0]["role"] == "main"

    def test_drm_markers_are_named_not_worked_around(self, isolated_db, fake_llm):
        page = VIDEO_HTML.replace("</body>", "<script>navigator.requestMediaKeySystemAccess("
                                             "'com.widevine.alpha', [])</script></body>")
        data, report = adaptive.identify_media(VIDEO_URL, page, FakeEngine(media_answer))
        assert "DRM_DETECTED" in data["protection"]
        assert any("won't decrypt" in c for c in data["confidence"]["media_resources"]["checks"])


# ---------------------------------------------------------------------------
# Exit 5 -- independent validation beats the model's self-reported confidence
# ---------------------------------------------------------------------------

class TestIndependentValidation:
    def test_implausible_length_is_rejected_despite_model_confidence(self, isolated_db, fake_llm,
                                                                      no_deterministic):
        h, u = _page(12)

        def answer(prompt):
            page = ax.PageModel(h, u)
            return {"body_blocks": [next(b.id for b in page.blocks if b.tag == "h3")],
                    "confidence": {"content": 0.99}}
        data, report = adaptive.extract_novel(h, u, FakeEngine(answer))
        assert data is None
        assert any("too short to be a chapter" in l for l in report.lines)

        page = ax.PageModel(h, u)
        tiny = ax.novel_data(page, [page.blocks[-1]], method="llm")
        ax.validate_novel(tiny, page, {"content": 0.99})
        assert tiny["confidence"]["content"]["bucket"] == "FAILED" and not tiny["valid"]
        huge = ax.novel_data(page, [ax.Block("x", "p", "字" * (ax.MAX_NOVEL_CHARS + 1))], method="llm")
        ax.validate_novel(huge, page, {"content": 0.99})
        assert "implausibly long" in huge["confidence"]["content"]["checks"][0] and not huge["valid"]

    def test_high_duplicate_rate_is_rejected(self, isolated_db):
        h, u = _page(12)
        page = ax.PageModel(h, u)
        nav = [b for b in page.blocks if b.link_only][:1] * 20 + \
            [b for b in page.blocks if b.tag == "p"][:3]
        data = ax.novel_data(page, nav, method="llm")
        ax.validate_novel(data, page, {"content": 1.0})
        assert not data["valid"] and any("repeats" in c for c in data["confidence"]["content"]["checks"])

        cands = ax.comic_candidates(
            "".join(f'<img src="https://c.example/p/{i}.jpg">' for i in range(4)), COMIC_URL)
        cdata = ax.comic_data(ax.PageModel("", COMIC_URL), cands, {c.url: "content" for c in cands},
                              method="llm", confidences={c.url: 0.99 for c in cands})
        same = {c.url: {"width": 800, "height": 1200, "sha256": "same"} for c in cands}
        ax.validate_comic(cdata, ax.PageModel("", COMIC_URL), same, {"page_images": 0.99})
        assert cdata["confidence"]["page_images"]["bucket"] == "FAILED" and not cdata["valid"]
        assert "same image" in cdata["confidence"]["page_images"]["checks"][0]

    def test_unrelated_next_link_is_rejected(self, isolated_db, fake_llm, no_deterministic):
        h, u = _page(12, extra_links='<a href="/forum/thread-5.html">下一篇热帖</a>')
        engine = FakeEngine(novel_answer(next_text="下一篇热帖",
                                         conf={"content": 0.95, "next_url": 0.99}))
        data, _ = adaptive.extract_novel(h, u, engine)
        assert data["next_url"] is None
        assert data["rejected"]["next_url"] == "https://www.novel.example/forum/thread-5.html"
        assert data["confidence"]["next_url"]["bucket"] == "FAILED"
        assert "doesn't look like another chapter" in data["confidence"]["next_url"]["checks"][0]
        # The real, same-shaped link is still accepted.
        assert data["previous_url"] == chapter_url(11)
        assert data["confidence"]["previous_url"]["bucket"] == "HIGH"

    @pytest.mark.parametrize("link,ok", [
        ("https://www.novel.example/book/77/1003.html", True),
        ("https://www.novel.example/book/77/", False),
        ("https://other.example/book/77/1003.html", False),
        ("https://www.novel.example/book/77/1002.html", False),
        ("javascript:void(0)", False),
    ])
    def test_link_resemblance(self, link, ok):
        assert (ax.link_resemblance(link, URL)[0] > 0) is ok


# ---------------------------------------------------------------------------
# Exit 6 -- a generated profile is validated before it's offered or saved
# ---------------------------------------------------------------------------

class TestProfileValidationBeforeSave:
    def test_llm_result_generates_a_validated_profile(self, isolated_db, fake_llm, no_deterministic):
        h, u = _page(12)
        _data, report = adaptive.extract_novel(h, u, FakeEngine(novel_answer()))
        assert report.profile["generated"] and report.profile["saved"] == 1
        v = profiles.active(DOMAIN, "novel")
        assert v["rules"]["content_selector"] == "div#content"
        assert v["rules"]["exclude_selectors"] == ["div.ad"]
        assert v["validation"]["overall"]["bucket"] == "HIGH" and v["origin"] == "llm"

    def test_a_candidate_that_fails_validation_is_not_saved(self, isolated_db, fake_llm,
                                                            no_deterministic, monkeypatch):
        h, u = _page(12)
        real = profiles.infer_novel_rules

        def bad_rules(page, data):
            rules = real(page, data)
            rules["content_selector"] = "div.nav"      # points at the menu, not the chapter
            return rules
        monkeypatch.setattr(profiles, "infer_novel_rules", bad_rules)
        data, report = adaptive.extract_novel(h, u, FakeEngine(novel_answer()))
        assert data["valid"]                                   # the extraction itself is fine
        assert report.profile["generated"] and report.profile["saved"] is False
        assert profiles.versions(DOMAIN) == [] and profiles.active(DOMAIN, "novel") is None
        assert any("NOT saved" in l for l in report.lines)

    def test_save_version_refuses_unvalidated_or_unapproved(self, isolated_db):
        rules = {"content_selector": "div#content"}
        with pytest.raises(profiles.ProfileRejected):
            profiles.save_version(DOMAIN, "novel", rules, {"valid": False, "problems": ["x"]}, "llm")
        medium = {"valid": True, "overall": {"score": 0.6, "bucket": "MEDIUM"}, "problems": []}
        with pytest.raises(profiles.ProfileRejected, match="approval"):
            profiles.save_version(DOMAIN, "novel", rules, medium, "llm")
        assert profiles.versions(DOMAIN) == []
        v = profiles.save_version(DOMAIN, "novel", rules, medium, "correction", approved=True)
        assert v["version"] == 1 and v["approved"]

    def test_medium_candidate_waits_for_approval(self, isolated_db, fake_llm, no_deterministic,
                                                 monkeypatch):
        h, u = _page(12)
        real = adaptive._novel_candidate_check

        def medium(page, source, rules):
            out = real(page, source, rules)
            out["overall"] = {"score": 0.6, "bucket": "MEDIUM"}
            return out
        monkeypatch.setattr(adaptive, "_novel_candidate_check", medium)
        _data, report = adaptive.extract_novel(h, u, FakeEngine(novel_answer()))
        assert report.profile["pending"] and report.pending_profile is not None
        assert profiles.active(DOMAIN, "novel") is None
        adaptive.approve_pending(report.pending_profile)
        assert profiles.active(DOMAIN, "novel")["approved"] is True


# ---------------------------------------------------------------------------
# Exit 7 -- a profile is reused without an LLM call; a broken one is kept
# ---------------------------------------------------------------------------

class TestProfileReuseAndVersioning:
    def test_reuse_then_break_then_replace_and_roll_back(self, isolated_db, fake_llm,
                                                         no_deterministic):
        engine = FakeEngine(novel_answer())
        h12, u12 = _page(12)
        _d, r1 = adaptive.extract_novel(h12, u12, engine)
        assert r1.extraction_tier == "llm" and len(engine.calls) == 1 and r1.profile["saved"] == 1

        # Next chapter, same site: the saved profile, no LLM call.
        h13, u13 = _page(13)
        d2, r2 = adaptive.extract_novel(h13, u13, engine)
        assert r2.extraction_tier == "profile" and r2.profile["used"] and r2.llm_calls == 0
        assert len(engine.calls) == 1
        assert d2["chapter_title"] == "第13章 重逢" and d2["next_url"] == chapter_url(14)
        assert d2["chapter_number"] == "13"
        assert not any("广告" in p["text"] for p in d2["paragraphs"])
        v1_rules = profiles.active(DOMAIN, "novel")["rules"]

        # The site is redesigned: the content container is renamed.
        h14, u14 = _page(14, container_id="chaptercontent", title_wrap="chapterhead")
        d3, r3 = adaptive.extract_novel(h14, u14, engine)
        assert r3.profile["existed"] and r3.profile["failed_version"] == 1
        assert r3.extraction_tier == "llm" and len(engine.calls) == 2    # full ladder ran
        assert r3.profile["saved"] == 2
        assert d3["valid"]

        vs = {v["version"]: v for v in profiles.versions(DOMAIN, "novel")}
        assert set(vs) == {1, 2}
        assert vs[1]["rules"] == v1_rules and vs[1]["status"] == "superseded"   # intact
        assert vs[1]["failures"] == 1 and "isn't on this page" in vs[1]["last_failure"]["reason"]
        assert vs[2]["status"] == "active" and vs[2]["replaces"] == 1
        assert vs[2]["rules"]["content_selector"] == "div#chaptercontent"

        # Roll back: v1 is active again, v2 is kept.
        profiles.rollback(DOMAIN, "novel", 1)
        assert profiles.active(DOMAIN, "novel")["version"] == 1
        assert len(profiles.versions(DOMAIN, "novel")) == 2
        _d, r4 = adaptive.extract_novel(h13, u13, engine)
        assert r4.extraction_tier == "profile" and len(engine.calls) == 2

    def test_candidate_needing_approval_leaves_the_old_profile_active(self, isolated_db, fake_llm,
                                                                    no_deterministic, monkeypatch):
        engine = FakeEngine(novel_answer())
        h12, u12 = _page(12)
        adaptive.extract_novel(h12, u12, engine)
        real = adaptive._novel_candidate_check

        def medium(page, source, rules):
            out = real(page, source, rules)
            out["overall"] = {"score": 0.6, "bucket": "MEDIUM"}
            return out
        monkeypatch.setattr(adaptive, "_novel_candidate_check", medium)
        h14, u14 = _page(14, container_id="chaptercontent")
        _d, r = adaptive.extract_novel(h14, u14, engine)
        assert r.profile["pending"] and profiles.active(DOMAIN, "novel")["version"] == 1

    def test_comic_profile_reused_without_llm_and_corrections_stick(self, isolated_db, fake_llm):
        from .sources_helpers import image
        engine = FakeEngine(lambda p: {"images": [
            {"id": d["id"], "role": "ad" if "promo" in d["url"] else "content", "page": None}
            for d in (json.loads(l) for l in p.split("IMAGES:\n", 1)[1].splitlines() if l.strip())]})

        def chapter(n):
            u = f"https://comic.example/read/{n}"
            body = ('<div id="reader">' + "".join(
                f'<img src="https://cdn.comic.example/c/{n}/{i:03d}.png">' for i in range(1, 4))
                + f'<img class="promo" src="https://cdn.comic.example/c/{n}/promo.png"></div>')
            routes = {u: html_resp(body)}
            routes.update({f"https://cdn.comic.example/c/{n}/{i:03d}.png": image(800, 1200, n * 10 + i)
                           for i in range(1, 4)})
            routes[f"https://cdn.comic.example/c/{n}/promo.png"] = image(800, 1200, 999 + n)
            return u, make_client("generic", ScriptedTransport(routes))

        u1, c1 = chapter(1)
        res1, r1 = adaptive.import_comic(u1, engine, client=c1)
        assert r1.extraction_tier == "llm" and r1.profile["saved"] == 1
        assert len(res1.images) == 3
        rules = profiles.active("comic.example", "comic")["rules"]
        assert "promo.png" in rules["excluded_names"]

        u2, c2 = chapter(2)
        res2, r2 = adaptive.import_comic(u2, engine, client=c2)
        assert r2.extraction_tier == "profile" and len(engine.calls) == 1
        assert [c.url.rsplit("/", 1)[-1] for c in res2.images] == ["001.png", "002.png", "003.png"]


# ---------------------------------------------------------------------------
# Exit 8 -- the extraction-result cache
# ---------------------------------------------------------------------------

class TestExtractionCache:
    def test_unchanged_page_skips_the_call_changed_page_reruns(self, isolated_db, fake_llm,
                                                             no_deterministic, monkeypatch):
        monkeypatch.setattr(profiles, "infer_novel_rules", lambda page, data: None)   # no profile
        engine = FakeEngine(novel_answer())
        h, u = _page(12)
        d1, r1 = adaptive.extract_novel(h, u, engine)
        d2, r2 = adaptive.extract_novel(h, u, engine)
        assert len(engine.calls) == 1 and r2.cache_hit and r2.llm_calls == 0
        assert d2["content"] == d1["content"]

        h_changed = h.replace("第12章第0段。", "第12章第0段（修订）。")
        _HTML_BY_URL[u] = h_changed
        _d3, r3 = adaptive.extract_novel(h_changed, u, engine)
        assert len(engine.calls) == 2 and not r3.cache_hit

    def test_entry_carries_provenance(self, isolated_db, fake_llm):
        engine = FakeEngine(media_answer)
        adaptive.identify_media(VIDEO_URL, VIDEO_HTML, engine)
        page = ax.PageModel(VIDEO_HTML, VIDEO_URL)
        h = ax.content_hash("video", ax.media_payload(page, ax.media_candidates(VIDEO_HTML, VIDEO_URL)))
        entry = store.get_extraction("video", h)
        assert entry["schema_version"] == ax.EXTRACTION_SCHEMA_VERSION
        assert entry["provider"] == "FakeEngine" and entry["model"] == "fake-model-1"
        assert {"model_version", "profile_version", "created_at", "confidence", "validation",
                "result", "picks"} <= set(entry)
        assert entry["validation"]["valid"] is True

    def test_schema_bump_invalidates_the_cached_entry(self, isolated_db, fake_llm, monkeypatch):
        engine = FakeEngine(media_answer)
        adaptive.identify_media(VIDEO_URL, VIDEO_HTML, engine)
        _d, r = adaptive.identify_media(VIDEO_URL, VIDEO_HTML, engine)
        assert r.cache_hit and len(engine.calls) == 1

        monkeypatch.setattr(ax, "EXTRACTION_SCHEMA_VERSION", ax.EXTRACTION_SCHEMA_VERSION + 1)
        _d, r = adaptive.identify_media(VIDEO_URL, VIDEO_HTML, engine)
        assert not r.cache_hit and len(engine.calls) == 2
        assert any("older format" in l for l in r.lines)
        _d, r = adaptive.identify_media(VIDEO_URL, VIDEO_HTML, engine)
        assert r.cache_hit and len(engine.calls) == 2      # re-cached under the new version


# ---------------------------------------------------------------------------
# Diagnostics log and the full import path
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_import_logs_tier_profile_and_reason(self, isolated_db, fake_llm, no_deterministic):
        h, u = _page(12)
        client = make_client("generic", ScriptedTransport({u: html_resp(h)}))
        res, report = adaptive.import_novel(u, FakeEngine(novel_answer()), client=client)
        assert res.text.startswith("第12章第0段。") and res.title == "第12章 重逢"
        rows = adaptive.recent_extractions()
        assert rows[0]["extraction_tier"] == "llm" and rows[0]["tier"] == "STATIC_HTTP"
        assert rows[0]["profile"]["saved"] == 1 and rows[0]["llm_calls"] == 1
        assert rows[0]["headline"] == "Worked: Static HTTP + AI-assisted extraction"
        assert "saved" in adaptive.describe_profile(rows[0]["profile"])

    def test_failure_is_explained_not_generic(self, isolated_db, no_deterministic):
        u = chapter_url(12)
        client = make_client("generic", ScriptedTransport({u: html_resp("<p>短</p>")}))
        with pytest.raises(generic_import.NoContentFound) as e:
            adaptive.import_novel(u, None, client=client)
        assert "AI engine" in str(e.value) and e.value.report.extraction_tier == "none"
        row = adaptive.recent_extractions()[0]
        assert row["reason"] and row["headline"].startswith("Failed")

    def test_llm_errors_are_redacted(self, isolated_db, no_deterministic, monkeypatch):
        import translate_engines

        def boom(engine, prompt, **kw):
            raise RuntimeError("401 for key sk-ant-api03-SECRETSECRETSECRETSECRET1234")
        monkeypatch.setattr(translate_engines, "call_llm_json", boom)
        h, u = _page(12)
        _d, report = adaptive.extract_novel(h, u, FakeEngine(None))
        assert "SECRETSECRET" not in " ".join(report.lines)
        assert any("AI engine call failed" in l for l in report.lines)


# ---------------------------------------------------------------------------
# The Sources tab: Review Extraction, diagnostics, the AI fallback picker
# ---------------------------------------------------------------------------


