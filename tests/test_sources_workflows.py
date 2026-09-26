"""
tests/test_sources_workflows.py -- Step 23's user-facing workflows:
scheduled chapter checks, multi-source search, the generic paste-a-URL
importers (comic + novel) and their content filter, the URL front door,
chapter ordering, and multi-chapter import into Scanlate's own pages.
"""

import os

import pytest

from sources import (chapter_check, chapter_order, front_door, generic_import, pipeline,
                     registry, store)
from sources.base import SourceAdapter
from sources.models import ChapterInfo, PageRef, SearchResult, SourceError, FailureReason

from .sources_helpers import FakeClock, FixedRng, ScriptedTransport, html, image, make_client, png


class FakeComicSource(SourceAdapter):
    """In-memory comic source. Chapters/pages are plain lists the test can
    mutate; downloads go through the real paced client and are recorded."""
    name = "fake_comic"
    display_name = "Fake Comic"
    content_types = ["manhua"]

    def __init__(self, client, chapters=None, fail_search=False, results=None):
        super().__init__(client)
        self.chapters = list(chapters or [])
        self.fail_search = fail_search
        self.results = results or []
        self.pages_requested = []
        self.downloaded = []

    def search(self, query, page=1):
        if self.fail_search:
            raise SourceError("search exploded", FailureReason.HTTP_ERROR)
        return [SearchResult(self.name, sid, title) for sid, title in self.results]

    def get_chapters(self, series_id):
        return [ChapterInfo(self.name, series_id, cid, title) for cid, title in self.chapters]

    def get_pages(self, chapter):
        self.pages_requested.append(chapter.chapter_id)
        return [PageRef(self.name, chapter.chapter_id, i,
                        f"https://img.fake.invalid/{chapter.chapter_id}/{i}.png") for i in range(2)]

    def download_page(self, page):
        self.downloaded.append(page.url)
        return self.client.get(page.url, classify_body=False).content, ".png"


def _named(cls_name, name):
    return type(cls_name, (FakeComicSource,), {"name": name, "display_name": name})


# ---------------------------------------------------------------------------
# Scheduled chapter checks
# ---------------------------------------------------------------------------

class TestChapterCheck:
    def test_new_chapter_is_notified_not_downloaded(self, isolated_db, monkeypatch):
        adapter = FakeComicSource(make_client("fake_comic", ScriptedTransport()),
                                  chapters=[("c1", "第1话"), ("c2", "第2话")])
        monkeypatch.setattr(registry, "is_enabled", lambda name: True)
        store.track_series("fake_comic", "s1", "Series", known_chapters=adapter.get_chapters("s1"))

        first = chapter_check.run_check_cycle(adapter_factory=lambda n: adapter)
        assert first["new"] == 0 and store.list_notifications() == []

        adapter.chapters.append(("c3", "第3话"))
        second = chapter_check.run_check_cycle(adapter_factory=lambda n: adapter)
        notes = store.list_notifications()
        assert second["new"] == 1
        assert [(n["chapter_id"], n["title"]) for n in notes] == [("c3", "第3话")]
        assert adapter.pages_requested == [] and adapter.downloaded == []
        assert second["queued"] == []

        third = chapter_check.run_check_cycle(adapter_factory=lambda n: adapter)
        assert third["new"] == 0                     # already known now

    def test_a_failing_source_is_recorded_not_raised(self, isolated_db, monkeypatch):
        class Broken(FakeComicSource):
            def get_chapters(self, series_id):
                raise SourceError("down", FailureReason.TIMEOUT)
        monkeypatch.setattr(registry, "is_enabled", lambda name: True)
        store.track_series("fake_comic", "s1", "Broken series")
        out = chapter_check.run_check_cycle(
            adapter_factory=lambda n: Broken(make_client("b", ScriptedTransport())))
        assert "Broken series" in out["errors"]
        assert store.list_tracked_series()[0]["last_check_error"]

    def test_check_due_follows_the_interval_and_zero_is_off(self, isolated_db):
        store.set_setting("check_interval_hours", 2)
        store.set_setting("last_check_cycle", 10_000.0)
        assert not chapter_check.check_due(now=10_000.0 + 3600)
        assert chapter_check.check_due(now=10_000.0 + 7201)
        store.set_setting("check_interval_hours", 0)
        assert not chapter_check.check_due(now=10**9)


# ---------------------------------------------------------------------------
# Multi-source search
# ---------------------------------------------------------------------------

class TestMultiSearch:
    def test_results_are_tagged_merged_and_a_failure_is_isolated(self, isolated_db):
        A = _named("A", "src_a")
        B = _named("B", "src_b")
        C = _named("C", "src_c")
        a = A(make_client("src_a", ScriptedTransport()), results=[("1", "Demo Comic"),
                                                                  ("2", "Only In A")])
        b = B(make_client("src_b", ScriptedTransport()), results=[("9", "demo  COMIC!")])
        c = C(make_client("src_c", ScriptedTransport()), fail_search=True)
        out = registry.multi_search("demo", adapters=[a, b, c])
        by_key = {r.key: r for r in out.results}
        assert by_key["democomic"].sources == ["src_a", "src_b"]
        assert by_key["onlyina"].sources == ["src_a"]
        assert "src_c" in out.errors and "src_a" not in out.errors
        assert out.per_source_counts == {"src_a": 2, "src_b": 1, "src_c": 0}

    def test_normalize_title(self):
        assert registry.normalize_title("Ｄｅｍｏ　Comic!") == registry.normalize_title("demo comic")


# ---------------------------------------------------------------------------
# Generic paste-a-URL comic import + content-vs-chrome filter
# ---------------------------------------------------------------------------

def _comic_site(chapter_url, extra_imgs=""):
    base = "https://comic.invalid"
    body = (f'<html><head><title>Some Comic 第5话</title></head><body>'
            f'<img src="{base}/static/logo.png">'
            f'<nav><img src="{base}/static/icon.png"></nav>'
            f'<img data-src="{base}/pages/{chapter_url[-1]}_1.jpg" src="data:image/gif;base64,R0lGOD">'
            f'<img data-original="{base}/pages/{chapter_url[-1]}_2.jpg">'
            f'<img srcset="{base}/pages/{chapter_url[-1]}_3_small.jpg 400w, '
            f'{base}/pages/{chapter_url[-1]}_3.jpg 800w">'
            f'<img src="{base}/promo/banner.jpg">'
            f'<img src="https://ads.elsewhere.invalid/creative.jpg">'
            f'{extra_imgs}</body></html>')
    return body


def _comic_routes(ch, logo_seed=1):
    base = "https://comic.invalid"
    return {
        f"{base}/read/{ch}": html(_comic_site(f"{base}/read/{ch}")),
        f"{base}/static/logo.png": image(500, 500, logo_seed),     # page-tall logo, same every chapter
        f"{base}/static/icon.png": image(48, 48, 2),                 # too small
        f"{base}/pages/{ch}_1.jpg": image(800, 1200, 10 + int(ch)),
        f"{base}/pages/{ch}_2.jpg": image(800, 1200, 20 + int(ch)),
        f"{base}/pages/{ch}_3.jpg": image(800, 1200, 30 + int(ch)),
        f"{base}/promo/banner.jpg": image(1600, 400, 40 + int(ch)),  # wrong shape, too short
        "https://ads.elsewhere.invalid/creative.jpg": image(800, 1200, 50 + int(ch)),
    }


class TestGenericComicImport:
    def test_extracts_page_images_in_reading_order(self, isolated_db):
        routes = _comic_routes("1")
        client = make_client("generic", ScriptedTransport(routes))
        res = generic_import.import_comic_page("https://comic.invalid/read/1", client=client)
        kept = [c.url.rsplit("/", 1)[-1] for c in res.images]
        assert kept == ["1_1.jpg", "1_2.jpg", "1_3.jpg"]            # srcset picked the big one
        assert [c.width for c in res.images] == [800, 800, 800]
        reasons = {c.url.rsplit("/", 1)[-1]: c.reject_reason for c in res.rejected}
        assert "too small" in reasons["icon.png"]
        assert "banner.jpg" in reasons
        assert "third-party" in reasons["creative.jpg"]
        assert "shape" in reasons["logo.png"]

    def test_cross_chapter_duplicate_is_rejected(self, isolated_db):
        routes = {**_comic_routes("1"), **_comic_routes("2")}
        client = make_client("generic", ScriptedTransport(routes))
        generic_import.import_comic_page("https://comic.invalid/read/1", client=client)
        res2 = generic_import.import_comic_page("https://comic.invalid/read/2", client=client)
        kept = [c.url.rsplit("/", 1)[-1] for c in res2.images]
        assert kept == ["2_1.jpg", "2_2.jpg", "2_3.jpg"]
        logo = next(c for c in res2.rejected if c.url.endswith("logo.png"))
        assert "other chapters" in logo.reject_reason

    def test_fails_cleanly_when_there_are_no_images(self, isolated_db):
        u = "https://comic.invalid/text-only"
        client = make_client("generic", ScriptedTransport({u: html("<p>" + "words " * 300 + "</p>")}))
        with pytest.raises(generic_import.NoContentFound) as e:
            generic_import.import_comic_page(u, client=client)
        assert "Couldn't find page images" in str(e.value)

    def test_fails_cleanly_when_every_image_is_furniture(self, isolated_db):
        u = "https://comic.invalid/icons"
        routes = {u: html('<img src="/a.png"><img src="/b.png">'),
                  "https://comic.invalid/a.png": image(40, 40, 1),
                  "https://comic.invalid/b.png": image(64, 64, 2)}
        client = make_client("generic", ScriptedTransport(routes))
        with pytest.raises(generic_import.NoContentFound):
            generic_import.import_comic_page(u, client=client)

    def test_filter_on_its_own(self):
        C = generic_import.ImageCandidate
        page = "https://s.invalid/ch/1"
        cands = [C("https://s.invalid/p1.jpg", 0, width=720, height=1080, sha256="a"),
                 C("https://s.invalid/icon.png", 1, width=32, height=32, sha256="b"),
                 C("https://s.invalid/p2.jpg", 2, width=720, height=1100, sha256="c"),
                 C("https://s.invalid/wide.jpg", 3, width=1500, height=420, sha256="d"),
                 C("https://s.invalid/logo.jpg", 4, width=720, height=1080, sha256="e"),
                 C("https://s.invalid/p3.jpg", 5, width=720, height=1060, sha256="f")]
        kept, rejected = generic_import.filter_page_images(cands, page, seen_elsewhere={"e"})
        assert [c.url.rsplit("/", 1)[-1] for c in kept] == ["p1.jpg", "p2.jpg", "p3.jpg"]
        why = {c.url.rsplit("/", 1)[-1]: c.reject_reason for c in rejected}
        assert "too small" in why["icon.png"]
        assert "shape" in why["wide.jpg"] or "too small" not in why["wide.jpg"]
        assert "other chapters" in why["logo.jpg"]

    def test_off_cluster_aspect_ratio_is_rejected(self):
        C = generic_import.ImageCandidate
        cands = [C(f"https://s.invalid/p{i}.jpg", i, width=700, height=1000, sha256=str(i))
                 for i in range(4)]
        cands.append(C("https://s.invalid/square.jpg", 9, width=1000, height=1000, sha256="sq"))
        kept, rejected = generic_import.filter_page_images(cands, "https://s.invalid/c")
        assert len(kept) == 4
        assert rejected[0].url.endswith("square.jpg") and "shape" in rejected[0].reject_reason


# ---------------------------------------------------------------------------
# Generic novel-text import
# ---------------------------------------------------------------------------

NOVEL_PAGE = ("<html><head><title>第12章 雨夜</title></head><body>"
              "<header><a href='/'>首页</a> <a href='/rank'>排行榜</a></header>"
              "<div class='sidebar'><ul><li>推荐一</li><li>推荐二</li></ul></div>"
              "<div id='content'>"
              + "".join(f"<p>这是第{i}段正文，雨下得很大，她站在门口等了很久很久。</p>" for i in range(40))
              + "</div><div class='comments'><p>评论：好看！</p></div>"
              "<footer>Copyright 2026</footer></body></html>")


class TestGenericNovelImport:
    @pytest.mark.parametrize("use_trafilatura", [True, False])
    def test_extracts_main_text_into_the_raw_novel_path(self, isolated_db, monkeypatch,
                                                        use_trafilatura):
        import builtins
        import core
        if use_trafilatura:
            pytest.importorskip("trafilatura")
        else:
            real_import = builtins.__import__

            def no_trafilatura(name, *a, **kw):
                if name == "trafilatura":
                    raise ImportError("not installed")
                return real_import(name, *a, **kw)
            monkeypatch.setattr(builtins, "__import__", no_trafilatura)
        u = "https://novel.invalid/book/1/12.html"
        client = make_client("generic", ScriptedTransport({u: html(NOVEL_PAGE)}))
        res = generic_import.import_novel_page(u, client=client)
        assert res.method == ("trafilatura" if use_trafilatura else "heuristic")
        assert "这是第0段正文" in res.text and "这是第39段正文" in res.text
        assert "排行榜" not in res.text and "Copyright" not in res.text
        assert res.title == "第12章 雨夜"

        drama_id = isolated_db.create_drama(title_zh="雨夜", media_type="novel")
        path = pipeline.save_novel_text(drama_id, res.text)
        assert path == os.path.join(isolated_db.drama_dir(drama_id), "raw_novel_context.txt")
        with open(path, encoding="utf-8") as f:
            saved = f.read()
        assert saved == core.load_novel_text_for_context(res.text.encode("utf-8"), "x.txt")

    def test_fails_cleanly_without_main_content(self, isolated_db):
        u = "https://novel.invalid/empty"
        page = "<html><body><nav>首页 排行</nav><footer>© 2026</footer></body></html>"
        client = make_client("generic", ScriptedTransport({u: html(page)}))
        with pytest.raises(generic_import.NoContentFound):
            generic_import.import_novel_page(u, client=client)

    def test_append_keeps_earlier_chapters(self, isolated_db):
        drama_id = isolated_db.create_drama(title_zh="x", media_type="novel")
        pipeline.save_novel_text(drama_id, "第一章内容", heading="第1章")
        path = pipeline.save_novel_text(drama_id, "第二章内容", append=True, heading="第2章")
        text = open(path, encoding="utf-8").read()
        assert text.index("第一章内容") < text.index("第二章内容")


# ---------------------------------------------------------------------------
# Front door
# ---------------------------------------------------------------------------

class TestFrontDoor:
    def test_classifies_video_novel_and_comic_urls_with_a_preview(self, isolated_db):
        video = front_door.preview("https://www.youtube.com/watch?v=abc123")
        assert video.content_type == front_door.VIDEO

        novel_url = "https://novel.invalid/book/1/12.html"
        comic_url = "https://comic.invalid/read/1"
        routes = {novel_url: html(NOVEL_PAGE), **_comic_routes("1")}
        client = make_client("generic", ScriptedTransport(routes))
        novel = front_door.preview(novel_url, client=client)
        assert novel.content_type == front_door.NOVEL
        assert novel.language == "zh" and novel.chapter == "第12章"

        comic = front_door.preview(comic_url, client=client)
        assert comic.content_type == front_door.COMIC
        assert comic.image_count >= 3 and "第5话" in comic.title

    def test_preview_imports_nothing(self, isolated_db):
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        client = make_client("generic", ScriptedTransport(_comic_routes("1")))
        front_door.preview("https://comic.invalid/read/1", client=client)
        assert isolated_db.list_pages(drama_id) == []

    def test_og_video_page_is_a_video(self):
        page = '<html><head><meta property="og:type" content="video.other"></head></html>'
        assert front_door.classify_html("https://x.invalid/v", page).content_type == front_door.VIDEO

    def test_detected_video_reaches_the_same_video_download_call(self, isolated_db, monkeypatch):
        import video_download
        calls = []

        def fake_download(url, out_dir, audio_only=True, progress_cb=None, title_cb=None,
                          cookies_browser=None, cookies_file=None):
            calls.append((url, out_dir, audio_only))
            title_cb("A stream title")
            path = os.path.join(out_dir, "downloaded_audio.wav")
            open(path, "wb").close()
            return path
        monkeypatch.setattr(video_download, "download", fake_download)
        drama_id = isolated_db.create_drama(media_type="streamer_vod")
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        assert front_door.preview(url).content_type == front_door.VIDEO
        front_door.import_video(url, drama_id)
        assert calls == [(url, isolated_db.drama_dir(drama_id), True)]
        d = isolated_db.get_drama(drama_id)
        assert d["audio_filename"] == "downloaded_audio.wav" and d["source_url"] == url
        assert d["title_zh"] == "A stream title"


# ---------------------------------------------------------------------------
# Chapter ordering
# ---------------------------------------------------------------------------

class TestChapterOrder:
    def test_numeric_not_lexicographic(self):
        titles = ["Chapter 10", "Chapter 2", "Chapter 1"]
        assert sorted(titles, key=lambda t: chapter_order.sort_key(t)) == \
            ["Chapter 1", "Chapter 2", "Chapter 10"]

    def test_mixed_cjk_and_named_sections(self):
        raw = ["第10话", "Epilogue", "第二話", "Prologue", "第1章", "番外 夏日", "第001話",
               "Chapter 3", "第十一话", "Side Story 1"]
        chapters = [ChapterInfo("s", "x", str(i), t) for i, t in enumerate(raw)]
        ordered = chapter_order.sort_chapters(chapters)
        assert [c.title for c in ordered] == ["Prologue", "第1章", "第001話", "第二話", "Chapter 3",
                                             "第10话", "第十一话", "Epilogue", "Side Story 1",
                                             "番外 夏日"]
        assert sorted(c.title for c in ordered) == sorted(raw)      # titles untouched
        assert all(c.sort_key for c in ordered)

    def test_volume_numbers_do_not_masquerade_as_chapters(self):
        assert chapter_order.chapter_number("第2卷 第5话") == 5
        assert chapter_order.sort_key("第2卷 第1话") > chapter_order.sort_key("第1卷 第9话")

    @pytest.mark.parametrize("s,n", [("十", 10), ("二十三", 23), ("一百零五", 105),
                                     ("〇〇七", 7), ("两千", 2000)])
    def test_chinese_numerals(self, s, n):
        assert chapter_order.cn_to_int(s) == n


# ---------------------------------------------------------------------------
# Multi-chapter import into Scanlate's own pipeline
# ---------------------------------------------------------------------------

class TestChapterImport:
    def test_only_selected_chapters_are_fetched_and_pacing_holds(self, isolated_db):
        clock = FakeClock()
        routes = {f"https://img.fake.invalid/{c}/{i}.png": image(600, 900, i + 3 * n)
                  for n, c in enumerate(["c1", "c2", "c3"]) for i in range(2)}
        t = ScriptedTransport(routes, clock)
        client = make_client("fake_comic", t, clock, min_delay=1.0, max_delay=3.0,
                             rng=FixedRng(0.0))
        adapter = FakeComicSource(client, chapters=[("c1", "第1话"), ("c2", "第2话"), ("c3", "第3话")])
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        selected = [c for c in adapter.get_chapters("s1") if c.chapter_id in ("c1", "c3")]

        import background_jobs
        job = "source_import_test"
        background_jobs.clear_job(job)
        background_jobs._jobs[job] = {"status": "running", "progress": 0.0, "message": "",
                                      "cancel_requested": False, "result": None}
        pipeline.run_import_job(job, "fake_comic", selected, drama_id, adapter=adapter)

        assert adapter.pages_requested == ["c1", "c3"]
        assert all("/c2/" not in u for u in t.urls())
        gaps = [b["t"] - a["t"] for a, b in zip(t.calls, t.calls[1:])]
        assert gaps and all(g >= 1.0 - 1e-9 for g in gaps)
        pages = isolated_db.list_pages(drama_id)
        assert [p["filename"] for p in pages] == [os.path.join("pages", f"page_{i:04d}.png")
                                                   for i in range(4)]
        result = background_jobs.get_status(job)["result"]
        assert [r["ok"] for r in result["chapters"]] == [True, True]
        assert result["stats"]["requests"] == 4
        background_jobs.clear_job(job)

    def test_cancel_stops_between_requests(self, isolated_db):
        import background_jobs
        routes = {f"https://img.fake.invalid/c1/{i}.png": image(600, 900, i) for i in range(2)}
        clock = FakeClock()
        t = ScriptedTransport(routes, clock)
        client = make_client("fake_comic", t, clock, min_delay=1.0, max_delay=1.0)
        adapter = FakeComicSource(client, chapters=[("c1", "第1话")])
        job = "source_import_cancel"
        background_jobs._jobs[job] = {"status": "running", "progress": 0.0, "message": "",
                                      "cancel_requested": False, "result": None}
        orig = adapter.download_page

        def download_then_cancel(page):
            out = orig(page)
            background_jobs.request_cancel(job)
            return out
        adapter.download_page = download_then_cancel
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        pipeline.run_import_job(job, "fake_comic", adapter.get_chapters("s"), drama_id,
                                adapter=adapter)
        result = background_jobs.get_status(job)["result"]
        assert result["cancelled"] and len(t.calls) == 1
        background_jobs.clear_job(job)

    def test_demo_source_pages_land_as_ordinary_scanlate_pages(self, isolated_db):
        from PIL import Image
        store.set_setting("demo_source_enabled", True)
        clock = FakeClock()
        demo = registry.get_adapter("demo", sleep=clock.sleep, clock=clock.clock)
        chapter = demo.get_chapters("1")[0]
        images = [demo.download_page(p) for p in demo.get_pages(chapter)]
        drama_id = isolated_db.create_drama(title_zh="演示", media_type="manhua")
        assert pipeline.add_page_images(drama_id, images) == 3
        for p in isolated_db.list_pages(drama_id):
            path = os.path.join(isolated_db.drama_dir(drama_id), p["filename"])
            with Image.open(path) as im:
                assert im.size == (p["width"], p["height"])

    def test_demo_challenge_series_hands_off(self, isolated_db):
        from sources.models import ChallengeDetected
        store.set_setting("demo_source_enabled", True)
        clock = FakeClock()
        demo = registry.get_adapter("demo", sleep=clock.sleep, clock=clock.clock)
        page = demo.get_pages(demo.get_chapters("2")[0])[0]
        with pytest.raises(ChallengeDetected):
            demo.download_page(page)

    def test_non_native_formats_are_converted_for_scanlate(self, isolated_db):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (300, 500), "white").save(buf, "WEBP")
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        pipeline.add_page_images(drama_id, [(buf.getvalue(), ".webp")])
        assert isolated_db.list_pages(drama_id)[0]["filename"].endswith(".png")

    def test_demo_source_hidden_until_enabled(self, isolated_db):
        assert not registry.is_enabled("demo")
        store.set_setting("demo_source_enabled", True)
        assert registry.is_enabled("demo")
