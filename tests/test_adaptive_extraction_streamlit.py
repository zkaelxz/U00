"""Streamlit widget, AppTest and tab-source tests split out of tests/test_adaptive_extraction.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_adaptive_extraction.py."""

import json
import pytest
from sources import adaptive, ai_extract as ax, generic_import, profiles
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


def _render_tab():
    import tabs.sources_tab as t
    t.render_sources_tab()


def _app(**session):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(_render_tab)
    for k, v in session.items():
        at.session_state[k] = v
    at.run(timeout=30)
    assert not at.exception, [e.value for e in at.exception]
    return at


def _btn(at, label):
    hits = [b for b in at.button if b.label == label]
    assert hits, f"no button {label!r}; have {[b.label for b in at.button]}"
    return hits[0]


@pytest.fixture
def quiet_scheduler(monkeypatch):
    from sources import chapter_check
    monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)


class TestSourcesTabReview:
    def test_novel_correction_is_saved_as_the_site_profile(self, isolated_db, quiet_scheduler):
        from sources import front_door
        drama = isolated_db.create_drama(title_zh="重逢", media_type="novel")
        h, u = _page(12)
        # A low-confidence result: the chapter plus the nav bar a dozen times over.
        page = ax.PageModel(h, u)
        nav = next(b for b in page.blocks if b.link_only)
        data = ax.validate_novel(ax.novel_data(
            page, [b for b in page.blocks if b.tag == "p"] + [nav] * 12, method="heuristic"), page)
        assert data["overall"]["bucket"] == "LOW"
        report = adaptive.ExtractionReport(u, "novel", extraction_tier="deterministic",
                                           needs_review=True, data=data)
        at = _app(src_fd_result=front_door.Preview(url=u, content_type="novel", title="第12章"),
                  src_fd_review={"kind": "novel", "url": u, "html": h, "data": data,
                                 "report": report, "drama_id": drama},
                  src_fd_report=report)
        assert any("Review extraction" in m.value for m in at.markdown)
        assert any("content" in c.value and "LOW" in c.value and "repeats" in c.value
                   for c in at.caption)
        at.selectbox(key="src_rv_container").set_value("div#content")
        at.run(timeout=30)
        at.multiselect(key="src_rv_exclude").set_value(["div.ad"])
        _btn(at, "🔁 Re-run with these corrections").click()
        at.run(timeout=30)
        assert not at.exception
        _btn(at, "💾 Save these corrections as the site's profile").click()
        at.run(timeout=30)
        v = profiles.active(DOMAIN, "novel")
        assert v["origin"] == "correction" and v["rules"]["exclude_selectors"] == ["div.ad"]
        # The correction is structure, not text: next chapter, profile, no AI.
        h13, u13 = _page(13)
        d13, r13 = adaptive.extract_novel(h13, u13, None)
        assert r13.extraction_tier == "profile"
        assert not any("广告" in p["text"] for p in d13["paragraphs"])

    def test_comic_image_marked_as_ad_sticks_on_the_next_fetch(self, isolated_db, quiet_scheduler):
        import os
        from sources import front_door
        from .sources_helpers import image
        drama = isolated_db.create_drama(title_zh="雨夜", media_type="manhua")

        def chapter(n):
            u = f"https://comic.example/read/{n}"
            body = ('<div id="reader">' + "".join(
                f'<img src="https://cdn.comic.example/c/{n}/{i:03d}.png">' for i in range(1, 4))
                + f'<div class="promo"><img src="https://cdn.comic.example/c/{n}/event_banner.png">'
                  '</div></div>')
            routes = {u: html_resp(body),
                      f"https://cdn.comic.example/c/{n}/event_banner.png": image(800, 1200, 500 + n)}
            routes.update({f"https://cdn.comic.example/c/{n}/{i:03d}.png": image(800, 1200, n * 10 + i)
                           for i in range(1, 4)})
            return u, make_client("generic", ScriptedTransport(routes))

        u1, c1 = chapter(1)
        res, report = adaptive.import_comic(u1, None, client=c1)
        assert report.needs_review and len(res.images) == 4       # the banner got through
        cands = res.images + res.rejected
        at = _app(src_fd_result=front_door.Preview(url=u1, content_type="comic"),
                  src_fd_review={"kind": "comic", "url": u1, "html": res.ladder.html,
                                 "data": report.data, "report": report, "drama_id": drama,
                                 "candidates": cands})
        banner = next(i for i, c in enumerate(cands) if "event_banner" in c.url)
        at.selectbox(key=f"src_rv_role_{banner}").set_value("ad")
        at.number_input(key=f"src_rv_pos_{banner}").set_value(0)
        _btn(at, "🔁 Apply these corrections").click()
        at.run(timeout=30)
        _btn(at, "💾 Save these corrections as the site's profile").click()
        at.run(timeout=30)
        rules = profiles.active("comic.example", "comic")["rules"]
        assert "event_banner.png" in rules["excluded_names"]
        _btn(at, "➕ Import these 3 page(s)").click()
        at.run(timeout=30)
        assert len(os.listdir(os.path.join(isolated_db.drama_dir(drama), "pages"))) == 3

        u2, c2 = chapter(2)
        res2, r2 = adaptive.import_comic(u2, None, client=c2)
        assert r2.extraction_tier == "profile"
        assert [c.url.rsplit("/", 1)[-1] for c in res2.images] == ["001.png", "002.png", "003.png"]

    def test_diagnostics_lists_attempts_and_rolls_back_profiles(self, isolated_db, quiet_scheduler,
                                                               fake_llm, no_deterministic):
        engine = FakeEngine(novel_answer())
        h12, u12 = _page(12)
        client = make_client("generic", ScriptedTransport({u12: html_resp(h12)}))
        adaptive.import_novel(u12, engine, client=client)
        h14, u14 = _page(14, container_id="chaptercontent")
        adaptive.extract_novel(h14, u14, engine)
        assert profiles.active(DOMAIN, "novel")["version"] == 2
        at = _app()
        assert any("Worked: Static HTTP + AI-assisted extraction" in m.value for m in at.markdown)
        assert any("novel v1 · superseded" in c.value and "last failed" in c.value
                   for c in at.caption)
        _btn(at, "Make active").click()
        at.run(timeout=30)
        assert profiles.active(DOMAIN, "novel")["version"] == 1

    def test_ai_fallback_is_off_by_default(self, isolated_db, quiet_scheduler):
        from sources import front_door
        isolated_db.create_drama(title_zh="重逢", media_type="novel")
        at = _app(src_fd_result=front_door.Preview(url=URL, content_type="novel"))
        assert at.selectbox(key="src_ai_engine").value == "off"
        assert "🤖 AI-assisted fallback (optional)" in [e.label for e in at.expander]
