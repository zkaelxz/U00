"""Chapter-to-line mapping (services/novel_line_chapters_service.py and
GET /api/novel/dramas/{id}/line-chapters): lines cut from saved chapters, and the
heading fallback. No network or models."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import novel_line_chapters_service as svc
from lib.errors import NotFoundError
from sources import pipeline
from sources.models import ChapterInfo


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _import(did, n, title, body):
    ch = ChapterInfo(source="xbanxia", series_id="s1", chapter_id=f"c{n}", title=title)
    assert pipeline._append_chapter_text("xbanxia", ch, did, body) == ""


def _lines(did, texts, en=()):
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=t, en=en[i] if i < len(en) else "")
                        for i, t in enumerate(texts)])


def test_saved_chapters_map_to_their_lines_and_count_translations(isolated_db):
    did = db.create_drama(title_en="D")
    _import(did, 1, "The Gate", "他走了。\n\n门关了。")
    _import(did, 2, "The Road", "路很长。")
    _lines(did, ["The Gate", "他走了。", "门关了。", "The Road", "路很长。"],
           en=["", "He left.", "", "", ""])
    out = svc.line_chapters(did)
    assert out["source"] == "saved" and out["total_lines"] == 5
    assert [(c["number"], c["first_idx"], c["line_count"], c["translated"]) for c in out["chapters"]] == [
        (1, 0, 3, 1), (2, 3, 2, 0)]


def test_a_chapter_whose_title_line_is_missing_does_not_lose_the_next(isolated_db):
    did = db.create_drama(title_en="D")
    _import(did, 1, "One", "甲。")
    _import(did, 2, "Two", "乙。")
    _import(did, 3, "Three", "丙。")
    _lines(did, ["One", "甲。", "乙。", "Three", "丙。"])
    assert [c["number"] for c in svc.line_chapters(did)["chapters"]] == [1, 3]


def test_headings_in_the_lines_are_used_without_saved_chapters(isolated_db):
    did = db.create_drama(title_en="D")
    _lines(did, ["第一章", "他走了。", "第二章", "路很长。", "夜深了。"])
    out = svc.line_chapters(did)
    assert out["source"] == "headings"
    assert [(c["title"], c["line_count"]) for c in out["chapters"]] == [("第一章", 2), ("第二章", 3)]


def test_no_chapters_and_unknown_title(isolated_db):
    did = db.create_drama(title_en="D")
    _lines(did, ["他走了。"])
    assert svc.line_chapters(did)["chapters"] == []
    with pytest.raises(NotFoundError):
        svc.line_chapters(9999)


def test_route_returns_rows(client, isolated_db):
    did = db.create_drama(title_en="D")
    _lines(did, ["第一章", "他走了。", "第二章", "路很长。"])
    r = client.get(f"/api/novel/dramas/{did}/line-chapters")
    assert r.status_code == 200 and len(r.json()["chapters"]) == 2
