"""The title's source link (dramas.source_url, set once at import) and each
chapter's source page link (comic chapters.json and the raw-novel manifest).
Mocked: no network, no models."""
import json
import os

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import background_jobs
import comic_chapters
import db
from services import drama_service
from sources import adaptive, chapter_manifest, pipeline
from sources.base import SourceAdapter
from sources.models import ChapterInfo, PageRef

from .sources_helpers import FakeClock, ScriptedTransport, image, make_client
from .test_adaptive_extraction import _HTML_BY_URL, fake_llm  # noqa: F401 (fixture)
from .test_api_sources_extraction import (COMIC_URL, SECRET, _comic_drama, _novel_page, _run,
                                          _wait, client, comic, env)  # noqa: F401


class TextSource(SourceAdapter):
    name = "fake_text"
    display_name = "Fake Text"
    content_types = ["novel"]

    def get_chapter_text(self, chapter):
        return f"{chapter.chapter_id} body"


class ComicSource(SourceAdapter):
    name = "fake_comic"
    display_name = "Fake Comic"
    content_types = ["manhua"]

    def get_pages(self, chapter):
        return [PageRef(self.name, chapter.chapter_id, 0, f"https://img.fake.invalid/{chapter.chapter_id}.png")]

    def download_page(self, page):
        return self.client.get(page.url, classify_body=False).content, ".png"


@pytest.fixture
def job():
    name = "source_import_link"
    background_jobs.clear_job(name)
    background_jobs._jobs[name] = {"status": "running", "progress": 0.0, "message": "",
                                   "cancel_requested": False, "result": None}
    yield name
    background_jobs.clear_job(name)


def _text_run(job, drama_id, series_url="", chapters=None):
    adapter = TextSource(make_client("fake_text", ScriptedTransport({})))
    chapters = chapters or [ChapterInfo("fake_text", "s1", "c1", "第1章",
                                        url="https://novel.example/b/1.html?sig=abc#x")]
    pipeline.run_import_job(job, "fake_text", chapters, drama_id, adapter=adapter,
                            series_url=series_url)


def _source_url(did):
    return db.get_drama(did)["source_url"] or ""


# ----- the title link ------------------------------------------------------

@pytest.mark.parametrize("given,stored", [
    ("https://user:pw@novel.example/book/7?token=abc#frag", "https://novel.example/book/7"),
    ("https://novel.example/dl/" + "a" * 40, "https://novel.example/"),
    ("ftp://novel.example/book", ""),
    ("javascript:alert(1)", ""),
    ("not a url", ""),
])
def test_set_source_url_once_stores_only_the_sanitised_link(isolated_db, given, stored):
    did = db.create_drama(title_en="N", media_type="novel")
    drama_service.set_source_url_once(did, given)
    assert _source_url(did) == stored


def test_set_source_url_once_never_overwrites(isolated_db):
    did = db.create_drama(title_en="N", media_type="novel", source_url="https://mine.example/x")
    assert drama_service.set_source_url_once(did, "https://novel.example/book/7") is False
    assert _source_url(did) == "https://mine.example/x"
    empty = db.create_drama(title_en="E", media_type="novel")
    assert drama_service.set_source_url_once(empty, "https://novel.example/a") is True
    assert drama_service.set_source_url_once(empty, "https://novel.example/b") is False
    assert _source_url(empty) == "https://novel.example/a"


def test_owner_link_saved_after_a_stale_read_survives(isolated_db, monkeypatch):
    did = db.create_drama(title_en="N", media_type="novel")
    stale = db.get_drama(did)
    db.update_drama(did, source_url="https://mine.example/x")
    with monkeypatch.context() as m:
        m.setattr(db, "get_drama", lambda _id: stale)
        assert drama_service.set_source_url_once(did, "https://novel.example/book/7") is False
    assert _source_url(did) == "https://mine.example/x"


def test_series_import_sets_the_link_once_and_keeps_the_owners(isolated_db, job):
    did = db.create_drama(title_en="N", media_type="novel")
    _text_run(job, did, series_url="https://novel.example/book/7?sig=abc")
    assert _source_url(did) == "https://novel.example/book/7"
    typed = db.create_drama(title_en="T", media_type="novel", source_url="https://mine.example/")
    _text_run(job, typed, series_url="https://novel.example/book/7")
    assert _source_url(typed) == "https://mine.example/"


def test_series_import_without_a_usable_link_stores_nothing(isolated_db, job):
    did = db.create_drama(title_en="N", media_type="novel")
    _text_run(job, did, series_url="file:///etc/passwd")
    assert _source_url(did) == ""


def test_pasted_novel_url_sets_the_link_without_the_query(client, env):
    url = _novel_page(env)
    signed = f"{url}?token={SECRET}"
    env["fetch"].pages[signed] = env["fetch"].pages[url]
    did = db.create_drama(title_en="N", media_type="novel")
    r = _run(client, "/api/sources/url/import", {"url": signed, "drama_id": did}, did)
    assert r.json()["result"]["needs_review"] is False
    assert _source_url(did) == url
    assert chapter_manifest.load(did)["chapters"][0]["url"] == url
    assert SECRET not in r.text


def test_pasted_comic_url_sets_the_link_and_labels_the_chapter(client, env, comic):
    did = _comic_drama()
    _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    assert _source_url(did) == "https://comic.example/read/77/5"
    groups = comic_chapters.group_pages(db.list_pages(did), comic_chapters.load(did))
    assert [(g["title"], g["url"], g["known"]) for g in groups] == [
        ("第5话", "https://comic.example/read/77/5", True)]
    assert SECRET not in json.dumps(groups)


def test_a_typed_link_survives_a_pasted_import(client, env, comic):
    did = db.create_drama(title_en="C", media_type="manhua", source_url="https://mine.example/c")
    _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    assert _source_url(did) == "https://mine.example/c"


def test_review_import_sets_the_link_and_chapter_link(client, env):
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    _run(client, "/api/sources/url/import", {"url": url, "drama_id": did, "review": True}, did)
    rv = client.get(f"/api/sources/dramas/{did}/extraction").json()
    r = client.post(f"/api/sources/dramas/{did}/extraction/import", json={"revision": rv["revision"]})
    assert r.status_code == 200
    _wait(f"sourceimport_{did}")
    assert _source_url(did) == url
    assert chapter_manifest.load(did)["chapters"][0]["url"] == url


@pytest.mark.parametrize("url,label", [
    ("https://site.example/read/chapter-12_final.html?x=1", "chapter 12 final"),
    ("https://site.example/%E7%AC%AC5%E8%AF%9D/", "第5话"),
    ("https://site.example/", ""),
    ("ftp://site.example/a", ""),
    ("https://site.example/%00ch%1b%7f-1", "ch 1"),
])
def test_title_from_url(url, label):
    assert adaptive.title_from_url(url) == label


# ----- per-chapter links ----------------------------------------------------

def test_novel_chapter_url_round_trips_sanitised(isolated_db, job):
    did = db.create_drama(title_en="N", media_type="novel")
    _text_run(job, did)
    row = chapter_manifest.load(did)["chapters"][0]
    assert row["url"] == "https://novel.example/b/1.html"
    with open(chapter_manifest.manifest_path(did), encoding="utf-8") as f:
        assert "sig=abc" not in f.read()


def test_old_novel_manifest_without_url_still_loads_and_hand_edits_are_sanitised(isolated_db, job):
    did = db.create_drama(title_en="N", media_type="novel")
    _text_run(job, did)
    path = chapter_manifest.manifest_path(did)
    data = json.load(open(path, encoding="utf-8"))
    del data["chapters"][0]["url"]
    json.dump(data, open(path, "w", encoding="utf-8"))
    assert chapter_manifest.load(did)["chapters"][0]["url"] == ""
    data["chapters"][0]["url"] = "https://u:p@novel.example/c?k=v"
    json.dump(data, open(path, "w", encoding="utf-8"))
    assert chapter_manifest.load(did)["chapters"][0]["url"] == "https://novel.example/c"
    data["chapters"][0]["url"] = ["not", "text"]
    json.dump(data, open(path, "w", encoding="utf-8"))
    assert chapter_manifest.load(did)["chapters"][0]["url"] == ""


def test_novel_chapter_api_returns_the_link(isolated_db, job):
    from services import novel_chapters_service as svc
    did = db.create_drama(title_en="N", media_type="novel")
    _text_run(job, did)
    assert svc.list_chapters(did)["chapters"][0]["url"] == "https://novel.example/b/1.html"
    assert svc.read_chapter(did, 1)["url"] == "https://novel.example/b/1.html"


def test_comic_chapter_url_round_trips_and_old_files_read(isolated_db, job):
    clock = FakeClock()
    routes = {"https://img.fake.invalid/c1.png": image(600, 900, 1)}
    adapter = ComicSource(make_client("fake_comic", ScriptedTransport(routes, clock), clock))
    did = db.create_drama(title_en="C", media_type="manhua")
    ch = ChapterInfo("fake_comic", "s1", "c1", "第1话", url="https://c.example/r/1?sig=abc")
    pipeline.run_import_job(job, "fake_comic", [ch], did, adapter=adapter)
    manifest = comic_chapters.load(did)
    assert manifest["chapters"][0]["url"] == "https://c.example/r/1"
    path = os.path.join(db.drama_dir(did), comic_chapters.MANIFEST_NAME)
    raw = json.load(open(path, encoding="utf-8"))
    assert "sig=abc" not in json.dumps(raw)
    del raw["chapters"][0]["url"]
    json.dump(raw, open(path, "w", encoding="utf-8"))
    groups = comic_chapters.group_pages(db.list_pages(did), comic_chapters.load(did))
    assert groups[0]["known"] is True and groups[0]["url"] == ""
    raw["chapters"][0]["url"] = "javascript:alert(1)"
    json.dump(raw, open(path, "w", encoding="utf-8"))
    assert comic_chapters.load(did)["chapters"][0]["url"] == ""


def test_unlabelled_pages_have_no_link(isolated_db):
    groups = comic_chapters.group_pages(
        [{"filename": "pages/page_0000.png"}], comic_chapters.load(db.create_drama(title_en="C")))
    assert groups[0]["known"] is False and groups[0]["url"] == ""
