"""
tests/test_sources_workflows.py -- Step 23's user-facing workflows:
scheduled chapter checks, multi-source search, the generic paste-a-URL
importers (comic + novel) and their content filter, the URL front door,
chapter ordering, and multi-chapter import into Scanlate's own pages.
"""

import os

import pytest

from sources import (adaptive, chapter_check, chapter_order, front_door, generic_import,
                     pipeline, registry, store)
from sources.base import SourceAdapter
from sources.models import ChapterInfo, PageRef, SearchResult, SourceError, FailureReason

from .sources_helpers import FakeClock, FixedRng, ScriptedTransport, html, image, make_client


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

    def test_a_source_with_many_hits_is_capped_at_the_per_source_limit(self, isolated_db):
        # Step 85: no result-per-source limit meant a source with a huge
        # catalog could return hundreds of hits for a broad query, with
        # no way to trim them.
        A = _named("A", "src_a")
        a = A(make_client("src_a", ScriptedTransport()),
             results=[(str(i), f"Result {i}") for i in range(50)])
        out = registry.multi_search("result", adapters=[a], limit_per_source=5)
        assert out.per_source_counts["src_a"] == 5
        assert len(out.results) == 5

    def test_default_limit_does_not_truncate_an_ordinary_result_count(self, isolated_db):
        A = _named("A", "src_a")
        a = A(make_client("src_a", ScriptedTransport()),
             results=[(str(i), f"Result {i}") for i in range(3)])
        out = registry.multi_search("result", adapters=[a])
        assert out.per_source_counts["src_a"] == 3

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
        res, report = adaptive.import_novel(u, client=client)
        assert report.extraction_tier == "deterministic" and report.llm_calls == 0
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
            adaptive.import_novel(u, client=client)

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
        # A video URL with no dedicated adapter (unlike Bilibili as of Step
        # 23d, which now routes through BilibiliSource -- see
        # TestBilibiliRouting below) still falls through to this same
        # generic yt-dlp path, unchanged.
        url = "https://www.youtube.com/watch?v=abc123def45"
        assert front_door.preview(url).content_type == front_door.VIDEO
        front_door.import_video(url, drama_id)
        assert calls == [(url, isolated_db.drama_dir(drama_id), True)]
        d = isolated_db.get_drama(drama_id)
        assert d["audio_filename"] == "downloaded_audio.wav" and d["source_url"] == url
        assert d["title_zh"] == "A stream title"


class TestBilibiliRouting:
    """Step 23d: a Bilibili URL now routes through the real BilibiliSource
    adapter instead of the generic video_download.download path."""

    def _fake_ydl_factory(self, info):
        import os as _os

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                if download:
                    path = self.opts["outtmpl"].replace("%(ext)s", info.get("ext", "mp4"))
                    _os.makedirs(_os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(b"fake")
                return info
        return lambda opts: FakeYDL(opts)

    def test_preview_shows_metadata_via_the_adapter_not_the_generic_video_branch(self, monkeypatch):
        from sources.adapters.bilibili import BilibiliSource
        info = {"id": "BV1xx411c7mD", "title": "A Real Bilibili Video",
               "webpage_url": "https://www.bilibili.com/video/BV1xx411c7mD",
               "duration": 60, "formats": []}
        monkeypatch.setattr(BilibiliSource, "_real_ydl_factory",
                            staticmethod(self._fake_ydl_factory(info)))
        p = front_door.preview("https://www.bilibili.com/video/BV1xx411c7mD")
        assert p.content_type == front_door.VIDEO
        assert p.adapter == "bilibili"
        assert p.title == "A Real Bilibili Video"

    def test_import_video_uses_the_adapters_download_not_the_generic_path(self, isolated_db, monkeypatch):
        import video_download
        from sources.adapters.bilibili import BilibiliSource

        generic_calls = []
        monkeypatch.setattr(video_download, "download",
                            lambda *a, **k: generic_calls.append(1) or "/should/not/be/used")

        info = {"id": "BV1xx411c7mD", "title": "A Real Bilibili Video",
               "webpage_url": "https://www.bilibili.com/video/BV1xx411c7mD",
               "duration": 60, "formats": [], "ext": "wav"}
        monkeypatch.setattr(BilibiliSource, "_real_ydl_factory",
                            staticmethod(self._fake_ydl_factory(info)))

        drama_id = isolated_db.create_drama(media_type="streamer_vod")
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        path = front_door.import_video(url, drama_id)

        assert generic_calls == []
        assert os.path.exists(path)
        d = isolated_db.get_drama(drama_id)
        assert d["source_url"] == url
        assert d["title_zh"] == "A Real Bilibili Video"


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

    def test_import_trims_the_cache_after_release(self, isolated_db, monkeypatch):
        """Roadmap 111: the size ceiling runs once the import's own
        temporary rows are released, even when the import is cancelled."""
        import background_jobs
        from sources import cache as cache_mod
        calls = []
        monkeypatch.setattr(cache_mod.RawCache, "release", lambda self: calls.append("release"))
        monkeypatch.setattr(cache_mod.RawCache, "enforce_ceiling",
                            lambda self: calls.append("ceiling"))
        clock = FakeClock()
        t = ScriptedTransport({"https://img.fake.invalid/c1/0.png": image(600, 900, 0)}, clock)
        adapter = FakeComicSource(make_client("fake_comic", t, clock), chapters=[("c1", "第1话")])
        job = "source_import_ceiling"
        background_jobs._jobs[job] = {"status": "running", "progress": 0.0, "message": "",
                                      "cancel_requested": True, "result": None}
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        pipeline.run_import_job(job, "fake_comic", adapter.get_chapters("s"), drama_id,
                                adapter=adapter)
        assert calls == ["release", "ceiling"]
        background_jobs.clear_job(job)

    def test_an_import_ending_keeps_another_running_imports_cache(self, isolated_db):
        """Import B finishing while import A is mid-chapter releases only
        B's temporary downloads: A's already-cached page stays cached."""
        import threading

        import background_jobs
        from sources import cache as cache_mod

        def importer(cid, seed):
            clock = FakeClock()
            routes = {f"https://img.fake.invalid/{cid}/{i}.png": image(600, 900, seed + i)
                      for i in range(2)}
            return FakeComicSource(make_client("fake_comic", ScriptedTransport(routes, clock),
                                               clock), chapters=[(cid, cid)])

        a, b = importer("a1", 0), importer("b1", 10)
        a_first = "https://img.fake.invalid/a1/0.png"
        seen = {}
        orig = a.download_page

        def download(page):
            if page.index == 1:   # A has cached page 0; B runs start to finish now
                t = threading.Thread(target=pipeline.run_import_job, args=(
                    "source_import_b", "fake_comic", b.get_chapters("s"), drama_b),
                    kwargs={"adapter": b})
                t.start()
                t.join()
                seen["a_cached"] = cache_mod.RawCache("temporary").get(a_first) is not None
            return orig(page)
        a.download_page = download

        drama_a = isolated_db.create_drama(title_zh="a", media_type="manhua")
        drama_b = isolated_db.create_drama(title_zh="b", media_type="manhua")
        for job in ("source_import_a", "source_import_b"):
            background_jobs._jobs[job] = {"status": "running", "progress": 0.0, "message": "",
                                          "cancel_requested": False, "result": None}
        pipeline.run_import_job("source_import_a", "fake_comic", a.get_chapters("s"), drama_a,
                                adapter=a)
        assert len(b.downloaded) == 2
        assert seen == {"a_cached": True}
        # Once both have ended, nothing temporary is left behind.
        with store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM cache_index").fetchone()[0] == 0
        for job in ("source_import_a", "source_import_b"):
            background_jobs.clear_job(job)

    def test_a_real_text_source_lands_in_the_novel_import_path_unchanged(self, isolated_db):
        """Step 23e's own manual-check pattern, run as a real automated
        test instead: a text-content adapter's get_chapter_text() output
        reaches Workspace's existing raw-novel file with no pipeline
        changes needed -- the run_import_job "else" branch (as opposed to
        the get_pages() branch every other test in this class exercises)
        had no direct test coverage before this."""
        import importlib
        fifty2shuku = importlib.import_module("sources.adapters.52shuku")

        toc_page = ("<html><head><title>A Novel - 52shuku</title></head><body>"
                   "<ul class='list clearfix'>"
                   "<li class='mulu'><a href='/x/b/1_1.html'>Chapter One</a></li>"
                   "</ul></body></html>")
        chapter_page = ("<html><body><article class='article-content'>"
                       "<div class='book_con fix' id='text'>"
                       "<p>Some imported chapter text.</p></div></article></body></html>")
        base = fifty2shuku.BASE_URL
        clock = FakeClock()
        t = ScriptedTransport({f"{base}/x/b/1.html": html(toc_page),
                              f"{base}/x/b/1_1.html": html(chapter_page)}, clock)
        client = make_client("52shuku", t, clock)
        adapter = fifty2shuku.FiftyTwoShukuSource(client=client)
        chapters = adapter.get_chapters("x/b/1.html")

        drama_id = isolated_db.create_drama(title_en="Imported Novel", media_type="novel",
                                            content_mode="novel_narration")
        import background_jobs
        job = "source_import_text_test"
        background_jobs.clear_job(job)
        background_jobs._jobs[job] = {"status": "running", "progress": 0.0, "message": "",
                                      "cancel_requested": False, "result": None}
        pipeline.run_import_job(job, "52shuku", chapters, drama_id, adapter=adapter)

        result = background_jobs.get_status(job)["result"]
        assert result["chapters"] == [{"chapter_id": "1", "title": "Chapter One",
                                       "ok": True, "chars": len("Some imported chapter text.")}]
        saved_path = os.path.join(isolated_db.drama_dir(drama_id), pipeline.RAW_NOVEL_FILENAME)
        assert os.path.exists(saved_path)
        with open(saved_path, encoding="utf-8") as f:
            saved_text = f.read()
        assert "Some imported chapter text." in saved_text
        assert "Chapter One" in saved_text  # the heading save_novel_text prepends
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


class _Crash(BaseException):
    """Stands in for the process dying: nothing in the pipeline catches it."""


class FakeTextSource(SourceAdapter):
    name = "fake_text"
    display_name = "Fake Text"
    content_types = ["novel"]

    def get_chapter_text(self, chapter):
        return f"{chapter.chapter_id} 的正文，只有一次。"


class TestChapterCommit:
    """A chapter is either written and recorded in imported_chapters, or
    left in the retry manifest in a state a retry can't duplicate."""

    @pytest.fixture
    def job(self):
        import background_jobs
        job = "source_import_commit"
        background_jobs.clear_job(job)
        background_jobs._jobs[job] = {"status": "running", "progress": 0.0, "message": "",
                                      "cancel_requested": False, "result": None}
        yield job
        background_jobs.clear_job(job)

    def _run(self, job, adapter, drama_id, *cids, title="第1章"):
        import background_jobs
        chapters = [ChapterInfo(adapter.name, "s1", c, title) for c in (cids or ("c1",))]
        pipeline.run_import_job(job, adapter.name, chapters, drama_id, adapter=adapter)
        return background_jobs.get_status(job)["result"]["chapters"]

    def _path(self, isolated_db, drama_id):
        return os.path.join(isolated_db.drama_dir(drama_id), pipeline.RAW_NOVEL_FILENAME)

    def _text(self, isolated_db, drama_id):
        with open(self._path(isolated_db, drama_id), encoding="utf-8") as f:
            return f.read()

    def _novel(self, isolated_db):
        return (isolated_db.create_drama(title_zh="x", media_type="novel"),
                FakeTextSource(make_client("fake_text", ScriptedTransport({}))))

    def _retry(self, source, drama_id):
        return [(r["chapter_id"], r["status"])
                for r in store.import_retry_rows(source, "s1", drama_id)]

    def _comic(self):
        clock = FakeClock()
        routes = {f"https://img.fake.invalid/c1/{i}.png": image(600, 900, i) for i in range(2)}
        return FakeComicSource(make_client("fake_comic", ScriptedTransport(routes, clock), clock),
                               chapters=[("c1", "第1话")])

    def _pages_on_disk(self, isolated_db, drama_id):
        pages_dir = os.path.join(isolated_db.drama_dir(drama_id), "pages")
        return sorted(os.listdir(pages_dir)) if os.path.isdir(pages_dir) else []

    def _crash_on(self, monkeypatch, obj, name):
        def crash(*a, **kw):
            raise _Crash()
        monkeypatch.setattr(obj, name, crash)

    def test_text_record_failure_is_retryable_and_reimport_does_not_duplicate(
            self, isolated_db, monkeypatch, job):
        drama_id, adapter = self._novel(isolated_db)
        real = store.record_imported

        def broken(*a, **kw):
            raise RuntimeError("sources.db is locked")
        monkeypatch.setattr(store, "record_imported", broken)
        rows = self._run(job, adapter, drama_id)
        assert rows == [{"chapter_id": "c1", "title": "第1章", "ok": False,
                         "error": pipeline._NOT_RECORDED}]
        assert store.imported_chapter_ids("fake_text", "s1", drama_id) == set()
        assert self._retry("fake_text", drama_id) == [("c1", "failed")]

        monkeypatch.setattr(store, "record_imported", real)
        assert self._run(job, adapter, drama_id)[0]["ok"] is True
        assert self._text(isolated_db, drama_id).count("c1 的正文") == 1
        assert store.imported_chapter_ids("fake_text", "s1", drama_id) == {"c1"}
        assert self._retry("fake_text", drama_id) == []

    def test_text_crash_before_the_record_leaves_a_clean_retry(self, isolated_db, monkeypatch,
                                                               job):
        drama_id, adapter = self._novel(isolated_db)
        pipeline.save_novel_text(drama_id, "前文", heading="序")
        real = store.record_imported
        self._crash_on(monkeypatch, store, "record_imported")
        with pytest.raises(_Crash):
            self._run(job, adapter, drama_id)
        assert self._retry("fake_text", drama_id) == [("c1", "failed")]

        monkeypatch.setattr(store, "record_imported", real)
        assert self._run(job, adapter, drama_id)[0]["ok"] is True
        text = self._text(isolated_db, drama_id)
        assert text.count("c1 的正文") == 1 and text.index("前文") < text.index("c1 的正文")

    def test_a_torn_append_is_cut_off_and_written_once(self, isolated_db, monkeypatch, job):
        drama_id, adapter = self._novel(isolated_db)
        path = pipeline.save_novel_text(drama_id, "前文", heading="序")
        real = pipeline._fsync
        self._crash_on(monkeypatch, pipeline, "_fsync")
        with pytest.raises(_Crash):
            self._run(job, adapter, drama_id)
        whole = open(path, "rb").read()
        with open(path, "r+b") as f:   # the crash cut the write short
            f.truncate(len(whole) - 7)

        monkeypatch.setattr(pipeline, "_fsync", real)
        assert self._run(job, adapter, drama_id)[0]["ok"] is True
        assert open(path, "rb").read() == whole

    def test_a_failed_append_leaves_the_file_unchanged_and_is_retryable(
            self, isolated_db, monkeypatch, job):
        drama_id, adapter = self._novel(isolated_db)
        path = pipeline.save_novel_text(drama_id, "前文", heading="序")
        before = open(path, "rb").read()
        real = pipeline._fsync

        def disk_full(fd):
            raise OSError("disk full")
        monkeypatch.setattr(pipeline, "_fsync", disk_full)
        assert self._run(job, adapter, drama_id) == [
            {"chapter_id": "c1", "title": "第1章", "ok": False, "error": pipeline._NOT_SAVED}]
        assert open(path, "rb").read() == before
        assert self._retry("fake_text", drama_id) == [("c1", "failed")]

        monkeypatch.setattr(pipeline, "_fsync", real)
        assert self._run(job, adapter, drama_id)[0]["ok"] is True
        assert self._text(isolated_db, drama_id).count("c1 的正文") == 1

    def test_a_torn_first_write_to_a_new_file_is_rewritten_without_a_separator(
            self, isolated_db, monkeypatch, job):
        drama_id, adapter = self._novel(isolated_db)
        path = self._path(isolated_db, drama_id)
        real = pipeline._fsync
        self._crash_on(monkeypatch, pipeline, "_fsync")
        with pytest.raises(_Crash):
            self._run(job, adapter, drama_id)
        whole = open(path, "rb").read()
        assert not whole.startswith(os.linesep.encode())
        with open(path, "r+b") as f:
            f.truncate(5)

        monkeypatch.setattr(pipeline, "_fsync", real)
        assert self._run(job, adapter, drama_id)[0]["ok"] is True
        assert open(path, "rb").read() == whole

    def test_a_failed_first_write_removes_the_new_file(self, isolated_db, monkeypatch, job):
        drama_id, adapter = self._novel(isolated_db)

        def disk_full(fd):
            raise OSError("disk full")
        monkeypatch.setattr(pipeline, "_fsync", disk_full)
        assert self._run(job, adapter, drama_id)[0]["error"] == pipeline._NOT_SAVED
        assert not os.path.exists(self._path(isolated_db, drama_id))
        rows = store.import_retry_rows("fake_text", "s1", drama_id)
        assert [(r["status"], r["error"]) for r in rows] == [("failed", pipeline._NOT_SAVED)]

    def test_an_old_sources_db_gains_text_offset(self, isolated_db):
        import sqlite3
        store.connect().close()
        with sqlite3.connect(store.db_path()) as conn:
            conn.execute("DROP TABLE import_retry")
            conn.execute("CREATE TABLE import_retry (source TEXT NOT NULL, series_id TEXT NOT NULL, "
                         "drama_id INTEGER NOT NULL, chapter_id TEXT NOT NULL, title TEXT NOT NULL "
                         "DEFAULT '', status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', "
                         "updated_at REAL NOT NULL, PRIMARY KEY (source, series_id, drama_id, "
                         "chapter_id))")
            conn.execute("INSERT INTO import_retry VALUES ('a', 's1', 1, 'c1', '', 'failed', '', 0)")
        for _ in range(2):   # the second connect finds the column already there
            with store.connect() as conn:
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(import_retry)")]
            assert cols.count("text_offset") == 1
        assert store.import_text_offset("a", "s1", 1, "c1") is None

    def test_append_writes_only_the_new_block(self, isolated_db, monkeypatch, job):
        import builtins
        drama_id, adapter = self._novel(isolated_db)
        path = pipeline.save_novel_text(drama_id, "前文", heading="序")
        before = open(path, "rb").read()
        real_open, modes = builtins.open, []

        def spy(file, mode="r", *a, **kw):
            if file == path:
                modes.append(mode)
            return real_open(file, mode, *a, **kw)
        monkeypatch.setattr(builtins, "open", spy)
        assert self._run(job, adapter, drama_id)[0]["ok"] is True
        monkeypatch.setattr(builtins, "open", real_open)
        assert modes == ["ab"]
        assert open(path, "rb").read().startswith(before)

    def test_distinct_chapters_with_identical_text_are_all_appended(self, isolated_db,
                                                                    monkeypatch, job):
        drama_id, adapter = self._novel(isolated_db)
        adapter.get_chapter_text = lambda chapter: "今日请假，明天补更。"
        rows = self._run(job, adapter, drama_id, "c1", "c2", title="请假条")
        assert [r["ok"] for r in rows] == [True, True]
        assert self._text(isolated_db, drama_id).count("今日请假") == 2

        real = store.record_imported
        self._crash_on(monkeypatch, store, "record_imported")
        with pytest.raises(_Crash):
            self._run(job, adapter, drama_id, "c3", title="请假条")
        monkeypatch.setattr(store, "record_imported", real)
        assert self._run(job, adapter, drama_id, "c3", title="请假条")[0]["ok"] is True
        assert self._text(isolated_db, drama_id).count("今日请假") == 3

    def test_comic_record_failure_removes_the_pages_so_a_retry_adds_them_once(
            self, isolated_db, monkeypatch, job):
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        real = store.record_imported

        def broken(*a, **kw):
            raise RuntimeError("sources.db is locked")
        monkeypatch.setattr(store, "record_imported", broken)
        rows = self._run(job, self._comic(), drama_id)
        assert rows[0]["ok"] is False and rows[0]["error"] == pipeline._NOT_RECORDED
        assert isolated_db.list_pages(drama_id) == []
        assert self._pages_on_disk(isolated_db, drama_id) == []
        assert self._retry("fake_comic", drama_id) == [("c1", "failed")]

        monkeypatch.setattr(store, "record_imported", real)
        assert self._run(job, self._comic(), drama_id)[0] == {
            "chapter_id": "c1", "title": "第1章", "ok": True, "pages": 2}
        assert len(isolated_db.list_pages(drama_id)) == 2
        assert store.imported_chapter_ids("fake_comic", "s1", drama_id) == {"c1"}
        assert self._retry("fake_comic", drama_id) == []

    def test_comic_crash_before_the_record_is_marked_partial(self, isolated_db, monkeypatch,
                                                            job):
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        self._crash_on(monkeypatch, store, "record_imported")
        with pytest.raises(_Crash):
            self._run(job, self._comic(), drama_id)
        # Pages a dead process wrote can't be taken back: never auto-retried.
        assert self._retry("fake_comic", drama_id) == [("c1", "partial")]

    def test_a_page_error_mid_chapter_leaves_no_pages_and_is_retryable(
            self, isolated_db, monkeypatch, job):
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        real = isolated_db.create_page
        calls = []

        def second_fails(*a, **kw):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return real(*a, **kw)
        monkeypatch.setattr(isolated_db, "create_page", second_fails)
        assert self._run(job, self._comic(), drama_id) == [
            {"chapter_id": "c1", "title": "第1章", "ok": False, "error": pipeline._ROLLED_BACK}]
        assert isolated_db.list_pages(drama_id) == []
        assert self._pages_on_disk(isolated_db, drama_id) == []
        assert self._retry("fake_comic", drama_id) == [("c1", "failed")]

    def test_a_failed_rollback_leaves_the_chapter_partial(self, isolated_db, monkeypatch, job):
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        real = isolated_db.create_page
        calls = []

        def second_fails(*a, **kw):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return real(*a, **kw)
        monkeypatch.setattr(isolated_db, "create_page", second_fails)

        def cannot_remove(*a, **kw):
            raise RuntimeError("library.db is locked")
        monkeypatch.setattr(pipeline, "_discard_pages", cannot_remove)
        with pytest.raises(RuntimeError):
            self._run(job, self._comic(), drama_id)
        assert len(isolated_db.list_pages(drama_id)) == 1
        assert self._retry("fake_comic", drama_id) == [("c1", "partial")]

    @pytest.mark.parametrize("comic", [False, True])
    def test_no_content_is_written_when_the_marker_cannot_be(self, isolated_db, monkeypatch,
                                                            job, comic):
        def broken(*a, **kw):
            raise RuntimeError("sources.db is read-only")
        monkeypatch.setattr(store, "mark_text_in_flight", broken)
        monkeypatch.setattr(store, "record_import_retry", broken)
        if comic:
            drama_id, adapter = isolated_db.create_drama(title_zh="x", media_type="manhua"), \
                self._comic()
        else:
            drama_id, adapter = self._novel(isolated_db)
        rows = self._run(job, adapter, drama_id)
        assert rows[0]["ok"] is False and rows[0]["error"] == pipeline._NO_BOOKKEEPING
        assert not os.path.exists(self._path(isolated_db, drama_id))
        assert isolated_db.list_pages(drama_id) == []


class TestTermsOfServiceBlocking:
    """Step 25g item 1: a real import folds its ladder run into the
    source's capability record, and a record whose terms block says the
    ToS prohibits automated access refuses before anything is sent."""

    def _prohibit(self, source):
        from sources import ladder
        caps = ladder.load_capabilities(source)
        caps.terms = {"checked": True, "tos_prohibited": True}
        ladder.save_capabilities(source, caps)

    def test_generic_import_records_the_ladder_run(self, isolated_db):
        from sources import ladder
        client = make_client("generic", ScriptedTransport(_comic_routes("1")))
        generic_import.import_comic_page("https://comic.invalid/read/1", client=client)
        caps = ladder.load_capabilities("generic")
        assert caps.tiers["STATIC_HTTP"].tested and caps.tiers["STATIC_HTTP"].ok
        assert caps.access_method == "STATIC_HTTP"

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_prohibited_source_is_refused_before_any_request(self, isolated_db):
        from sources.models import TermsProhibited
        self._prohibit("generic")
        t = ScriptedTransport(_comic_routes("1"))
        with pytest.raises(TermsProhibited) as e:
            generic_import.import_comic_page("https://comic.invalid/read/1",
                                             client=make_client("generic", t))
        assert e.value.reason == FailureReason.TOS_PROHIBITED
        from sources import adaptive
        with pytest.raises(TermsProhibited):
            adaptive.import_novel("https://comic.invalid/read/1", client=make_client("generic", t))
        with pytest.raises(TermsProhibited):
            adaptive.import_comic("https://comic.invalid/read/1", client=make_client("generic", t))
        assert t.calls == []

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_a_pasted_link_uses_the_matching_adapters_record(self, isolated_db, monkeypatch):
        from sources.models import TermsProhibited

        class ProhibitedSite(SourceAdapter):
            name = "tos_site"
            url_patterns = [r"tos-site\.invalid/"]

            def capabilities(self):
                caps = super().capabilities()
                caps.terms = {"checked": True, "tos_prohibited": True}
                return caps
        monkeypatch.setitem(registry._ADAPTERS, "tos_site", ProhibitedSite)
        from sources import adaptive
        registry.set_enabled("tos_site", False)   # switched off: its terms still apply
        with pytest.raises(TermsProhibited):
            adaptive.import_comic("https://tos-site.invalid/ch/1")
        with pytest.raises(TermsProhibited):
            adaptive.import_novel("https://tos-site.invalid/ch/1")
        registry.set_enabled("tos_site", True)
        with pytest.raises(TermsProhibited):
            front_door.preview("https://tos-site.invalid/ch/1")

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_multi_chapter_import_is_refused(self, isolated_db):
        import background_jobs
        routes = {f"https://img.fake.invalid/c1/{i}.png": image(600, 900, i) for i in range(2)}
        t = ScriptedTransport(routes)
        adapter = FakeComicSource(make_client("fake_comic", t),
                                  chapters=[("c1", "第1话"), ("c2", "第2话")])
        self._prohibit("fake_comic")
        drama_id = isolated_db.create_drama(title_zh="x", media_type="manhua")
        job = "source_import_tos"
        background_jobs._jobs[job] = {"status": "running", "progress": 0.0, "message": "",
                                      "cancel_requested": False, "result": None}
        pipeline.run_import_job(job, "fake_comic", adapter.get_chapters("s"), drama_id,
                                adapter=adapter)
        result = background_jobs.get_status(job)["result"]
        assert len(result["chapters"]) == 1 and not result["chapters"][0]["ok"]
        assert "TOS_PROHIBITED" in result["chapters"][0]["error"]
        assert t.calls == [] and isolated_db.list_pages(drama_id) == []
        background_jobs.clear_job(job)

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_a_stale_stored_record_cant_clear_a_corrected_built_in_prohibition(self, isolated_db):
        """Step 25q gap 1: an earlier import already stored a clean
        record. The adapter's own built-in default has since been
        corrected to prohibited -- the stale stored `False` must not
        keep overriding it."""
        from sources import ladder
        from sources.models import SourceCapabilities, TermsProhibited
        # A real earlier import recorded a clean, un-prohibited result.
        ladder.record_ladder_result("fake_comic", ladder.LadderResult(
            url="https://comic.invalid/read/1", tier="STATIC_HTTP",
            technical_status="SUPPORTED", capability_status="VERIFIED"))
        assert not ladder.load_capabilities("fake_comic").terms.get("tos_prohibited")
        # The adapter's own built-in default is now corrected to prohibited.
        corrected_default = SourceCapabilities(platform="Fake Comic",
                                               terms={"tos_prohibited": True})
        with pytest.raises(TermsProhibited):
            ladder.check_terms("fake_comic", corrected_default)

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_multi_search_skips_a_prohibited_source(self, isolated_db):
        A = _named("A", "src_a")
        B = _named("B", "src_b")
        a = A(make_client("src_a", ScriptedTransport()), results=[("1", "Demo Comic")])
        b = B(make_client("src_b", ScriptedTransport()), results=[("9", "Also Demo")])
        self._prohibit("src_b")
        out = registry.multi_search("demo", adapters=[a, b])
        assert out.per_source_counts.get("src_a") == 1
        assert out.per_source_counts.get("src_b", None) in (0, None)
        assert [r.source for m in out.results for r in m.entries] == ["src_a"]
        assert "src_b" in out.errors and "TOS_PROHIBITED" in out.errors["src_b"]

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_tracked_series_checker_skips_a_prohibited_source(self, isolated_db, monkeypatch):
        adapter = FakeComicSource(make_client("fake_comic", ScriptedTransport()),
                                  chapters=[("c1", "第1话"), ("c2", "第2话")])
        monkeypatch.setattr(registry, "is_enabled", lambda name: True)
        store.track_series("fake_comic", "s1", "Series")
        self._prohibit("fake_comic")
        out = chapter_check.run_check_cycle(adapter_factory=lambda n: adapter)
        assert out["checked"] == 0 and out["new"] == 0
        assert "Series" in out["errors"] and "TOS_PROHIBITED" in out["errors"]["Series"]
        assert adapter.chapters and store.list_notifications() == []

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_test_tier_refuses_a_prohibited_source(self, isolated_db):
        """Step 28 gap 1: the Sources tab's "Test Now" diagnostic buttons
        went straight to the tier function with no check_terms() call --
        the one network-touching action path in the package that didn't
        already have one."""
        from sources import ladder
        from sources.models import AccessTier, TermsProhibited
        self._prohibit("fake_comic")
        t = ScriptedTransport(_comic_routes("1"))
        with pytest.raises(TermsProhibited):
            ladder.test_tier("fake_comic", AccessTier.STATIC_HTTP,
                             "https://comic.invalid/read/1",
                             ladder.static_tier(make_client("fake_comic", t)))
        assert t.calls == []
        caps = ladder.load_capabilities("fake_comic")
        assert not caps.tiers.get("STATIC_HTTP") or not caps.tiers["STATIC_HTTP"].tested

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_import_video_refuses_a_prohibited_video_adapter(self, isolated_db, monkeypatch):
        """Step 28 gap 2: front_door.import_video() dispatched straight to
        adapter.download() with no check_terms() call of its own, unlike
        pipeline.run_import_job's per-chapter re-check."""
        from sources.adapters.bilibili import BilibiliSource
        from sources.models import TermsProhibited
        self._prohibit("bilibili")
        downloaded = []
        monkeypatch.setattr(BilibiliSource, "download",
                            lambda self, *a, **k: downloaded.append(1) or {"path": "/x"})
        drama_id = isolated_db.create_drama(media_type="streamer_vod")
        with pytest.raises(TermsProhibited):
            front_door.import_video("https://www.bilibili.com/video/BV1xx411c7mD", drama_id)
        assert downloaded == []
