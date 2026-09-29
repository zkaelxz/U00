"""
Library parity routes (react-misc-parity): the Continue reading shelf, the
data-driven "All dramas" filter choices and the PC-only reading-history
clear. FastAPI TestClient against an isolated library -- no network.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def test_continue_lists_partial_progress_only(client):
    a = db.create_drama(title_en="Half")
    b = db.create_drama(title_en="Done")
    c = db.create_drama(title_en="Untouched")
    db.save_progress(a, last_page=3, percent_complete=40.0)
    db.save_progress(b, percent_complete=100.0)
    db.update_drama(a, cover_art_filename="cover.png")
    items = client.get("/api/library/continue").json()["items"]
    assert [i["drama_id"] for i in items] == [a]
    row = items[0]
    assert row["title_en"] == "Half" and row["percent_complete"] == 40.0
    assert row["last_page"] == 3 and row["has_cover_art"] is True
    assert "cover_art_filename" not in row
    assert c not in [i["drama_id"] for i in items]
    assert client.get("/api/library/continue?limit=0").status_code == 422


def test_filter_options(client):
    db.create_drama(title_en="A", studio="Studio B", author="Au", voice_actors="X, Y",
                    custom_tags="Favorite,cozy")
    db.create_drama(title_en="B", studio="Studio A", voice_actors="Y")
    body = client.get("/api/library/filter-options").json()
    assert body == {"studios": ["Studio A", "Studio B"], "authors": ["Au"],
                    "voice_actors": ["X", "Y"], "custom_tags": ["Favorite", "cozy"]}


def test_list_dramas_honours_every_filter(client):
    a = db.create_drama(title_en="A", studio="S1", author="Au", voice_actors="X",
                        source_language="ja", media_type="anime", custom_tags="cozy")
    db.create_drama(title_en="B", studio="S2", source_language="zh", media_type="novel")
    for params in ({"studio": "S1"}, {"author": "Au"}, {"voice_actor": "X"},
                   {"source_language": "ja"}, {"media_type": "anime"}, {"tag": "cozy"}):
        items = client.get("/api/library/dramas", params=params).json()["items"]
        assert [i["id"] for i in items] == [a], params


def test_clear_history_needs_confirm_and_keeps_progress(client):
    a = db.create_drama(title_en="A")
    db.save_progress(a, last_line_idx=2, percent_complete=30.0)
    db.save_progress(a, last_line_idx=4, percent_complete=50.0)
    assert len(client.get("/api/library/history").json()["items"]) == 2
    assert client.post("/api/library/history/clear", json={}).status_code == 422
    assert client.post("/api/library/history/clear", json={"confirm": "yes"}).status_code == 422
    r = client.post("/api/library/history/clear", json={"confirm": True})
    assert r.status_code == 200 and r.json() == {"cleared": True, "removed": 2}
    assert client.get("/api/library/history").json()["items"] == []
    # Progress (and so the Continue shelf) survives.
    assert [i["drama_id"] for i in client.get("/api/library/continue").json()["items"]] == [a]


def test_clear_history_is_pc_only(isolated_db):
    a = db.create_drama(title_en="A")
    db.save_progress(a, last_line_idx=1, percent_complete=10.0)
    remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                        raise_server_exceptions=False)
    assert remote.post("/api/library/history/clear", json={"confirm": True}).status_code in (401, 403)
    assert len(db.list_reading_history()) == 1
