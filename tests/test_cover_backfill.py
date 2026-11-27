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
