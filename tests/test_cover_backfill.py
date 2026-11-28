"""Covers for titles that have none: page one of a comic, the fill order, the library pass, and the
cover a Sources import leaves."""
import io
import os

import pytest

import db
from services import cover_art_service, cover_backfill_service, sources_import_service
from tests.job_helpers import wait_for_job

PIL = pytest.importorskip("PIL")


def _png(color="red", size=(40, 60)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _comic(pages=2, title="C"):
    did = db.create_drama(title_en=title, media_type="manhua")
    for i in range(pages):
        name = f"pages/p{i}.png"
        os.makedirs(os.path.join(db.drama_dir(did), "pages"), exist_ok=True)
        with open(os.path.join(db.drama_dir(did), name), "wb") as f:
            f.write(_png(("red", "blue")[i % 2]))
        db.create_page(did, i, name, 40, 60)
    return did


def test_first_page_becomes_the_cover(isolated_db):
    did = _comic()
    result = cover_art_service.make_cover_from_first_page(did)
    assert result["has_cover_art"] is True
    assert db.get_drama(did)["cover_art_filename"] == "cover.png"


def test_a_title_with_no_pages_is_refused(isolated_db):
    did = db.create_drama(title_en="N", media_type="novel")
    with pytest.raises(cover_art_service.InvalidInputError):
        cover_art_service.make_cover_from_first_page(did)


def test_fill_never_replaces_a_cover(isolated_db):
    did = _comic()
    cover_art_service.save_cover(did, io.BytesIO(_png("green")))
    before = cover_art_service.cover_stamp(did)
    assert cover_art_service.fill_missing_cover(did) is False
    assert cover_art_service.cover_stamp(did) == before


def test_site_picture_comes_before_page_one(isolated_db, monkeypatch):
    did = _comic()
    from services import quick_read_fetch
    monkeypatch.setattr(quick_read_fetch, "fetch_one", lambda url, headers, cap: (_png("green", (30, 30)), ""))
    assert cover_art_service.fill_missing_cover(did, "https://cdn.example/c.png", "https://site.example/s") is True
    path, _ = cover_art_service.cover_file(did)
    from PIL import Image
    assert Image.open(path).size == (30, 30)


def test_failed_site_picture_falls_back_to_page_one(isolated_db, monkeypatch):
    did = _comic()
    from services import quick_read_fetch
    monkeypatch.setattr(quick_read_fetch, "fetch_one", lambda url, headers, cap: (None, "refused"))
    assert cover_art_service.fill_missing_cover(did, "https://cdn.example/c.png", "") is True
    path, _ = cover_art_service.cover_file(did)
    from PIL import Image
    assert Image.open(path).size == (40, 60)


def test_backfill_fills_only_titles_without_a_cover(isolated_db):
    bare = _comic(title="Bare")
    empty = db.create_drama(title_en="Empty", media_type="novel")
    has = _comic(title="Has")
    cover_art_service.save_cover(has, io.BytesIO(_png("green")))
    kept = cover_art_service.cover_stamp(has)
    assert cover_backfill_service.start_backfill()["started"] is True
    status = wait_for_job(cover_backfill_service.JOB_ID)
    assert status["result"] == {"checked": 2, "filled": 1}
    assert db.get_drama(bare)["cover_art_filename"] == "cover.png"
    assert db.get_drama(empty)["cover_art_filename"] is None
    assert cover_art_service.cover_stamp(has) == kept


def test_the_startup_pass_runs_once(isolated_db):
    _comic()
    cover_backfill_service.start_backfill_once()
    wait_for_job(cover_backfill_service.JOB_ID)
    assert os.path.exists(os.path.join(db.LIBRARY_DIR, ".covers_backfilled"))
    started = []
    orig = cover_backfill_service.start_backfill
    cover_backfill_service.start_backfill = lambda: started.append(1)
    try:
        cover_backfill_service.start_backfill_once()
    finally:
        cover_backfill_service.start_backfill = orig
    assert started == []


class _Info:
    cover_url = "https://cdn.example/c.png"
    url = "https://site.example/s"


class _Adapter:
    def supports(self, name):
        return True

    def get_series(self, series_id):
        return _Info()


def test_a_source_import_leaves_the_site_cover(isolated_db, monkeypatch):
    did = db.create_drama(title_en="S", media_type="manhua")
    from services import quick_read_fetch
    monkeypatch.setattr(quick_read_fetch, "fetch_one", lambda url, headers, cap: (_png("green", (30, 30)), ""))
    sources_import_service._fill_cover(_Adapter(), "s1", did)
    assert db.get_drama(did)["cover_art_filename"] == "cover.png"


def test_a_failing_source_never_fails_the_import(isolated_db):
    class Broken(_Adapter):
        def get_series(self, series_id):
            raise RuntimeError("down")
    did = db.create_drama(title_en="S", media_type="manhua")
    sources_import_service._fill_cover(Broken(), "s1", did)
    assert db.get_drama(did)["cover_art_filename"] is None


def test_cover_url_comes_from_the_meta_tags_only():
    html = ('<script>"og:image"</script><meta property="og:title" content="T">'
            '<meta content="/c/s.jpg?a=1&amp;b=2" property="og:image">')
    assert cover_art_service.cover_url_from_html(html, "https://site.example/series/1") == \
        "https://site.example/c/s.jpg?a=1&b=2"
    assert cover_art_service.cover_url_from_html('<meta name="twitter:image" content="javascript:x">', "https://s.example/") == ""
    assert cover_art_service.cover_url_from_html("<p>none</p>", "https://s.example/") == ""


def test_a_url_import_cover_prefers_the_page_picture(isolated_db, monkeypatch):
    did = _comic()
    from services import quick_read_fetch
    seen = []
    monkeypatch.setattr(quick_read_fetch, "fetch_one", lambda url, h, cap: (seen.append(url), (_png("green", (30, 30)), ""))[1])
    url = cover_art_service.cover_url_from_html('<meta property="og:image" content="https://cdn.example/c.png">', "https://s.example/")
    assert cover_art_service.fill_missing_cover(did, url, "https://s.example/") is True
    assert seen == ["https://cdn.example/c.png"]


class _Resp:
    def __init__(self, content=b"", text=""):
        self.content, self.text = content, text


class _FakeClient:
    """Stands in for the source's paced client: a page with an og:image, then the picture."""
    source = "generic"

    def __init__(self, picture=None, page="", fail=False):
        self.picture, self.page, self.fail, self.requests = picture, page, fail, []

    def get(self, url, **kw):
        self.requests.append(url)
        if self.fail:
            raise RuntimeError("down")
        return _Resp(content=self.picture or b"", text=self.page)


_PAGE = '<meta property="og:image" content="https://cdn.example/c.png">'


def _linked_comic(title, cover="auto"):
    did = _comic(title=title)
    if cover == "auto":
        cover_art_service.make_cover_from_first_page(did)
    elif cover == "own":
        cover_art_service.save_cover(did, io.BytesIO(_png("white", (40, 60))))
    db.update_drama(did, source_url="https://site.example/s")
    return did


def test_refresh_replaces_the_cover_through_the_paced_client(isolated_db):
    did = _linked_comic("A")
    client = _FakeClient(_png("yellow", (30, 45)), _PAGE)
    assert cover_art_service.refresh_site_cover(did, client) == "fetched"
    from PIL import Image
    assert Image.open(cover_art_service.cover_file(did)[0]).size == (30, 45)
    assert client.requests == ["https://site.example/s", "https://cdn.example/c.png"]


def test_refresh_reports_what_happened_and_keeps_the_cover(isolated_db):
    did = _linked_comic("A")
    before = cover_art_service.cover_stamp(did)
    assert cover_art_service.refresh_site_cover(did, _FakeClient(page="<p>no picture</p>")) == "kept"
    assert cover_art_service.refresh_site_cover(did, _FakeClient(fail=True)) == "unreachable"
    assert cover_art_service.refresh_site_cover(did, _FakeClient(None, _PAGE)) == "unreachable"
    assert cover_art_service.refresh_site_cover(did, _FakeClient(_png("yellow", (90, 30)), _PAGE)) == "kept"
    assert cover_art_service.cover_stamp(did) == before
    bare = _comic(title="B")
    assert cover_art_service.refresh_site_cover(bare, _FakeClient()) == "no_link"


def test_the_bulk_pass_skips_a_chosen_cover_and_is_capped(isolated_db, monkeypatch):
    auto = _linked_comic("Auto")
    own = _linked_comic("Own", cover="own")
    kept = cover_art_service.cover_stamp(own)
    clients = []

    def make(link):
        clients.append(_FakeClient(_png("yellow", (30, 45)), _PAGE))
        return clients[-1]
    monkeypatch.setattr(cover_art_service, "paced_client", make)
    cover_backfill_service.start_site_refresh()
    status = wait_for_job(cover_backfill_service.SITE_JOB_ID)
    assert status["result"] == {"checked": 1, "fetched": 1, "left": 0}
    assert cover_art_service.cover_stamp(own) == kept
    from PIL import Image
    assert Image.open(cover_art_service.cover_file(auto)[0]).size == (30, 45)


def test_a_title_whose_page_names_nothing_is_not_asked_again(isolated_db, monkeypatch):
    did = _linked_comic("A")
    monkeypatch.setattr(cover_art_service, "paced_client", lambda link: _FakeClient(page="<p>x</p>"))
    cover_backfill_service.start_site_refresh()
    wait_for_job(cover_backfill_service.SITE_JOB_ID)
    assert did not in cover_backfill_service._site_candidates()


def test_the_startup_pass_never_calls_a_site(isolated_db, monkeypatch):
    _linked_comic("A")
    monkeypatch.setattr(cover_art_service, "paced_client", lambda link: pytest.fail("a site was asked"))
    cover_backfill_service.start_backfill()
    wait_for_job(cover_backfill_service.JOB_ID)


def test_a_landscape_site_picture_does_not_replace_page_one(isolated_db, monkeypatch):
    from services import quick_read_fetch
    did = _comic()
    cover_art_service.make_cover_from_first_page(did)
    before = cover_art_service.cover_stamp(did)
    monkeypatch.setattr(quick_read_fetch, "fetch_one", lambda url, h, cap: (_png("yellow", (90, 30)), ""))
    assert cover_art_service.fetch_site_cover(did, "https://cdn.example/logo.png", "", replace=True) is False
    assert cover_art_service.cover_stamp(did) == before


def test_the_refresh_route_answers_with_the_outcome(isolated_db, monkeypatch):
    from api.api_config import ApiSettings
    from api.server import create_app
    from fastapi.testclient import TestClient
    did = _linked_comic("A")
    monkeypatch.setattr(cover_art_service, "paced_client",
                        lambda link: _FakeClient(_png("yellow", (30, 45)), _PAGE))
    api = TestClient(create_app(ApiSettings()), raise_server_exceptions=False, headers={"X-Baihe-Local": "1"})
    r = api.post(f"/api/dramas/{did}/cover/from-site")
    assert r.status_code == 200, r.text
    assert r.json() == {"drama_id": did, "outcome": "fetched"}
    assert api.post("/api/dramas/9999/cover/from-site").status_code == 404


def _vod(cover=True):
    did = db.create_drama(title_en="V", media_type="video")
    db.update_drama(did, source_url="https://www.youtube.example/watch")
    if cover:
        cover_art_service.save_cover(did, io.BytesIO(_png("white", (40, 60))))
    return did


def test_a_video_takes_the_platform_thumbnail_even_when_wide(isolated_db, monkeypatch):
    did = _vod()
    monkeypatch.setattr(cover_art_service, "_video_thumbnail_url", lambda link: "https://i.example/t.jpg")
    client = _FakeClient(_png("yellow", (160, 90)))
    assert cover_art_service.refresh_site_cover(did, client) == "fetched"
    from PIL import Image
    assert Image.open(cover_art_service.cover_file(did)[0]).size == (160, 90)
    assert client.requests == ["https://i.example/t.jpg"]


def test_a_video_falls_back_to_the_page_picture(isolated_db, monkeypatch):
    did = _vod(cover=False)
    monkeypatch.setattr(cover_art_service, "_video_thumbnail_url", lambda link: "")
    client = _FakeClient(_png("yellow", (160, 90)), _PAGE)
    assert cover_art_service.refresh_site_cover(did, client) == "fetched"
    assert client.requests == ["https://www.youtube.example/watch", "https://cdn.example/c.png"]


def test_a_video_with_no_picture_anywhere_keeps_its_cover(isolated_db, monkeypatch):
    did = _vod()
    before = cover_art_service.cover_stamp(did)
    monkeypatch.setattr(cover_art_service, "_video_thumbnail_url", lambda link: "")
    assert cover_art_service.refresh_site_cover(did, _FakeClient(page="<p>x</p>")) == "kept"
    assert cover_art_service.refresh_site_cover(did, _FakeClient(fail=True)) == "unreachable"
    assert cover_art_service.cover_stamp(did) == before
