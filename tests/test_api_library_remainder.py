"""
Tests for the Library remainder endpoints (Migration E0): stats, recent,
costs, series, search, history, presets, voice bank and the two renames.
FastAPI TestClient against an isolated library -- no network.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _drama(title, series=None, lines=()):
    did = db.create_drama(title_en=title, series_id=series)
    if lines:
        db.save_lines(did, [Line(idx=i, start=0.0, end=1.0, zh=zh, en=en)
                            for i, (zh, en) in enumerate(lines)])
    return did


def test_stats_and_costs(client):
    a = _drama("A", lines=[("你好", "Hello"), ("再见", "")])
    _drama("B")
    db.log_usage(a, "claude", "m", "translate", 100, 50, 0.5, cache_read_tokens=40)
    body = client.get("/api/library/stats").json()
    assert body["total_dramas"] == 2 and body["total_lines"] == 2 and body["translated_lines"] == 1
    assert body["usage"]["call_count"] == 1 and body["usage"]["cache_read_tokens"] == 40
    items = client.get("/api/library/costs").json()["items"]
    assert [i["id"] for i in items] == [a]
    assert len(client.get("/api/library/recent?limit=1").json()["items"]) == 1
    assert client.get("/api/library/recent?limit=0").status_code == 422


def test_series_needs_two_dramas(client):
    s1, s2 = db.get_or_create_series("One"), db.get_or_create_series("Two")
    _drama("x", s1)
    _drama("y", s2)
    _drama("z", s2)
    items = client.get("/api/library/series").json()["items"]
    assert [i["name"] for i in items] == ["Two"]
    assert len(items[0]["dramas"]) == 2


def test_search_and_history(client):
    a = _drama("A", lines=[("你好世界", "Hello world")])
    body = client.get("/api/library/search", params={"q": "world"}).json()
    assert body["count"] == 1 and body["items"][0]["drama_id"] == a
    assert client.get("/api/library/search", params={"q": "%' OR 1=1 --"}).json()["count"] == 0
    assert client.get("/api/library/search", params={"q": ""}).status_code == 422
    assert client.get("/api/library/search", params={"q": "x" * 201}).status_code == 422
    assert client.get("/api/library/history").json() == {"items": []}
    db.save_progress(a, last_line_idx=0, percent_complete=10.0)
    hist = client.get("/api/library/history").json()["items"]
    assert hist and hist[0]["drama_id"] == a
    assert client.get("/api/library/history?limit=101").status_code == 422


def test_presets_list_and_rename(client):
    p1 = db.save_preset("Alpha", translation_engine="claude")
    p2 = db.save_preset("Beta")
    items = client.get("/api/library/presets").json()["items"]
    assert [i["name"] for i in items] == ["Alpha", "Beta"]
    r = client.post(f"/api/library/presets/{p1}/rename", json={"name": "  Gamma "})
    assert r.status_code == 200 and r.json()["name"] == "Gamma"
    assert r.json()["translation_engine"] == "claude"
    assert client.post(f"/api/library/presets/{p1}/rename", json={"name": "Beta"}).status_code == 409
    assert client.post("/api/library/presets/9999/rename", json={"name": "Q"}).status_code == 404
    assert client.post(f"/api/library/presets/{p2}/rename", json={"name": "   "}).status_code == 422
    assert client.post(f"/api/library/presets/{p2}/rename", json={"name": "x", "z": 1}).status_code == 422
    assert client.post(f"/api/library/presets/{2**31}/rename", json={"name": "Q"}).status_code == 422


def test_voice_bank_hides_paths_and_renames(client, tmp_path):
    clip = tmp_path / "c.wav"
    clip.write_bytes(b"RIFF")
    eid = db.save_voice_bank_entry("Voice", str(clip), language="zh")
    items = client.get("/api/library/voice-bank").json()["items"]
    assert items[0]["clip_available"] is True
    assert "clip_filename" not in items[0] and "ref_text" not in items[0]
    r = client.post(f"/api/library/voice-bank/{eid}/rename", json={"name": "New"})
    assert r.status_code == 200 and r.json()["name"] == "New"
    assert client.post("/api/library/voice-bank/9999/rename", json={"name": "N"}).status_code == 404
