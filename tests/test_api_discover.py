"""Discover catalog API (Migration Slice 55). Mocked; no network or LLM."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app

BODY = {"title_original": "余情", "title_en": "Lingering", "language": "zh",
        "media_type": "audio_drama"}


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _code(r):
    return r.json()["error"]["code"]


def test_crud_flow(client):
    r = client.post("/api/discover/titles", json=BODY)
    assert r.status_code == 201
    tid = r.json()["id"]
    g = client.get("/api/discover/titles", params={"q": "Linger", "language": "zh"}).json()
    assert g["total"] == 1 and g["titles"][0]["id"] == tid
    assert client.post(f"/api/discover/titles/{tid}/delete", json={"confirm": True}).json() == \
        {"deleted": True, "id": tid}
    assert client.get("/api/discover/titles").json()["titles"] == []


def test_create_rejects_unknown_and_invalid(client):
    assert client.post("/api/discover/titles", json={**BODY, "id": 3}).status_code == 422
    assert client.post("/api/discover/titles", json={**BODY, "media_type": "x"}).status_code == 422
    assert client.post("/api/discover/titles", json={**BODY, "language": "en"}).status_code == 422
    assert client.post("/api/discover/titles",
                       json={**BODY, "title_en": "x" * 301}).status_code == 422
    assert db.list_known_titles() == []


def test_delete_confirm_and_404(client):
    tid = client.post("/api/discover/titles", json=BODY).json()["id"]
    assert client.post(f"/api/discover/titles/{tid}/delete", json={}).status_code == 422
    assert client.post(f"/api/discover/titles/{tid}/delete",
                       json={"confirm": False}).status_code == 422
    assert len(db.list_known_titles()) == 1
    r = client.post("/api/discover/titles/999/delete", json={"confirm": True})
    assert r.status_code == 404 and _code(r) == "not_found"


def test_seed_idempotent(client):
    a = client.post("/api/discover/titles/seed").json()
    b = client.post("/api/discover/titles/seed").json()
    assert a["added"] > 0 and b["added"] == 0 and a["total"] == b["total"]


def test_import_and_duplicate_409(client):
    tid = client.post("/api/discover/titles", json=BODY).json()["id"]
    r = client.post(f"/api/discover/titles/{tid}/import-to-library")
    assert r.status_code == 201
    drama_id = r.json()["id"]
    r2 = client.post(f"/api/discover/titles/{tid}/import-to-library")
    assert r2.status_code == 409 and _code(r2) == "conflict"
    assert r2.json()["error"]["details"] == {"drama_id": drama_id}
    assert client.post("/api/discover/titles/777/import-to-library").status_code == 404


def test_platforms_and_search_links(client):
    p = client.get("/api/discover/platforms", params={"language": "zh"})
    assert p.status_code == 200 and p.json()["platforms"]
    assert client.get("/api/discover/platforms", params={"language": "q"}).status_code == 422
    s = client.get("/api/discover/search-links", params={"q": "女将军", "format": "novel"})
    assert s.status_code == 200 and s.json()["links"]
    assert client.get("/api/discover/search-links", params={"q": ""}).status_code == 422
    assert client.get("/api/discover/search-links",
                      params={"q": "x", "format": "zz"}).status_code == 422


def test_no_path_or_secret_in_responses(client, isolated_db):
    client.post("/api/discover/titles/seed")
    tid = client.post("/api/discover/titles", json=BODY).json()["id"]
    texts = [
        client.get("/api/discover/titles").text,
        client.get("/api/discover/platforms").text,
        client.post(f"/api/discover/titles/{tid}/import-to-library").text,
        client.post(f"/api/discover/titles/{tid}/import-to-library").text,
        client.post("/api/discover/titles/999/delete", json={"confirm": True}).text,
    ]
    for t in texts:
        assert str(isolated_db) not in t
        assert "/home/" not in t and "api_key" not in t.lower()
