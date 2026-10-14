"""The reader's chapter list from the title's source: the extension saves the chapter links a
page showed (extension-only sites), an adapter site lists its own chapters. Mocked: no network."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import comic_chapter_links
import comic_chapters
import db
import page_server
from api.api_config import ApiSettings
from api.server import create_app
from services import comic_source_chapters_service as svc
from sources.models import ChapterInfo, SourceError

from tests.test_page_server import _post

PAGE = "https://reader.example/series/abc"
LINKS = [{"title": "Chapter 1", "url": "https://reader.example/series/abc/1?token=x"},
         {"title": "Chapter 2", "url": "https://reader.example/series/abc/2"},
         {"title": "Chapter 3", "url": "https://elsewhere.example/3"},
         {"title": "Chapter 2 again", "url": "https://reader.example/series/abc/2"},
         {"title": "", "url": "https://reader.example/series/abc/4"}]


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def token(isolated_db):
    return page_server.load_or_create_token()


def _send(token, links, url=PAGE):
    return _post(token, {"url": url, "titles": ["Healing Spell - Reader"], "chapter_links": links},
                 path="/title")


def test_clean_keeps_this_sites_links_once_without_query():
    assert comic_chapter_links.clean(LINKS, "reader.example") == [
        {"title": "Chapter 1", "url": "https://reader.example/series/abc/1"},
        {"title": "Chapter 2", "url": "https://reader.example/series/abc/2"}]
    assert comic_chapter_links.clean("not a list", "reader.example") == []


def test_extension_links_become_the_chapter_list(token, client):
    h = _send(token, LINKS)
    assert h.status == 200, h.payload
    drama_id = h.payload["drama_id"]
    body = client.get(f"/api/scanlate/dramas/{drama_id}/source-chapters").json()
    assert body["mode"] == "links"
    assert [c["title"] for c in body["chapters"]] == ["Chapter 1", "Chapter 2"]
    assert all(not c["imported"] and c["url"].startswith("https://reader.example/") for c in body["chapters"])


def test_a_chapter_already_in_the_reader_is_marked_imported(token, client):
    drama_id = _send(token, LINKS).payload["drama_id"]
    ref = comic_chapters.chapter_ref("2", "Chapter 2", "reader.example", "https://reader.example/series/abc/2")
    comic_chapters.record_pages(drama_id, ref, ["pages/page_0000.png"])
    chapters = client.get(f"/api/scanlate/dramas/{drama_id}/source-chapters").json()["chapters"]
    assert [c["imported"] for c in chapters] == [False, True]


def test_a_chapter_page_with_few_links_adds_to_the_list(token, client):
    drama_id = _send(token, LINKS).payload["drama_id"]
    _send(token, [{"title": "Chapter 3", "url": "https://reader.example/series/abc/3"},
                  {"title": "Chapter 2", "url": "https://reader.example/series/abc/2"}])
    assert [c["title"] for c in comic_chapter_links.load(drama_id)] == ["Chapter 1", "Chapter 2", "Chapter 3"]


def test_no_links_and_no_adapter_is_mode_none(client):
    d = db.create_drama(title_en="Plain", media_type="manga")
    assert client.get(f"/api/scanlate/dramas/{d}/source-chapters").json()["mode"] == "none"


def test_an_unknown_title_is_404(client):
    assert client.get("/api/scanlate/dramas/999/source-chapters").status_code == 404


class _Adapter:
    name = "fake"

    def __init__(self, fail=False):
        self.fail = fail

    def supports(self, method):
        return True

    def parse_url(self, url):
        return ("series", "s1")

    def get_chapters(self, series_id):
        if self.fail:
            raise SourceError("boom https://secret.example/?k=1")
        return [ChapterInfo(source="fake", series_id=series_id, chapter_id=str(i), title=f"Ch {i}") for i in (1, 2)]


def _use_adapter(monkeypatch, adapter):
    class Cls:
        name = "fake"

    monkeypatch.setattr(svc.registry, "adapter_class_for_url", lambda url: Cls)
    monkeypatch.setattr(svc.registry, "is_enabled", lambda name: True)
    monkeypatch.setattr(svc.extension_marker, "is_marked", lambda name: False)
    monkeypatch.setattr(svc.registry, "find_for_url", lambda url, **kw: adapter)
    monkeypatch.setattr(svc.chapter_order, "reading_order", lambda a, chapters: list(chapters))


def test_an_adapter_site_lists_its_chapters_with_imported_marks(client, monkeypatch):
    d = db.create_drama(title_en="Site", media_type="manga", source_url="https://site.example/s/1")
    _use_adapter(monkeypatch, _Adapter())
    monkeypatch.setattr(svc.store, "imported_chapter_ids", lambda *a: {"1"})
    body = client.get(f"/api/scanlate/dramas/{d}/source-chapters").json()
    assert (body["mode"], body["source"], body["series_id"]) == ("adapter", "fake", "s1")
    assert [(c["chapter_id"], c["imported"]) for c in body["chapters"]] == [("1", True), ("2", False)]


def test_a_failed_listing_is_503_without_the_error_text(client, monkeypatch):
    d = db.create_drama(title_en="Site", media_type="manga", source_url="https://site.example/s/1")
    _use_adapter(monkeypatch, _Adapter(fail=True))
    r = client.get(f"/api/scanlate/dramas/{d}/source-chapters")
    assert r.status_code == 503 and "secret.example" not in r.text
