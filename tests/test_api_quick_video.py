"""Routes of /api/quick-video: the profile choice, a refused start that creates nothing, and the
watch-once guard (only a recorded title is removable)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.server import create_app
from services import profile_service, url_media_service

JSON = {"X-Baihe-Local": "1"}


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(url_media_service, "check_link", lambda url: url)
    return TestClient(create_app())


def _profile(**options):
    return db.save_preset("Quick", translation_engine="ollama", options_json=profile_service.dump_options(options))


def test_the_chosen_profile_round_trips_and_a_missing_one_is_404(client):
    pid = _profile(quick_source_language="ko")
    out = client.post("/api/quick-video/settings", json={"profile_id": pid}, headers=JSON).json()
    assert out["profile_id"] == pid and out["profiles"] == [{"id": pid, "name": "Quick", "ready": True}]
    assert client.post("/api/quick-video/settings", json={"profile_id": pid + 1}, headers=JSON).status_code == 404
    assert client.get("/api/quick-video/settings").json()["profile_id"] == pid


def test_a_start_without_a_profile_is_refused_and_creates_no_title(client):
    r = client.post("/api/quick-video/start", json={"url": "https://example.com/v"}, headers=JSON)
    assert r.status_code == 422 and "Settings" in r.json()["error"]["message"]
    assert db.list_dramas() == []


def test_a_title_that_was_not_made_watch_once_cannot_be_removed(client):
    did = db.create_drama(title_en="Keep")
    r = client.post(f"/api/quick-video/{did}/delete", json={"confirm": True}, headers=JSON)
    assert r.status_code == 422 and db.get_drama(did) is not None
