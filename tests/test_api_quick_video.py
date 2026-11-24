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
    # A fresh install has the Default profile; "None" in Settings is the way to have no profile.
    assert client.post("/api/quick-video/settings", json={"profile_id": None}, headers=JSON).status_code == 200
    r = client.post("/api/quick-video/start", json={"url": "https://example.com/v"}, headers=JSON)
    assert r.status_code == 422 and "Settings" in r.json()["error"]["message"]
    assert db.list_dramas() == []


def test_a_title_that_was_not_made_watch_once_cannot_be_removed(client):
    did = db.create_drama(title_en="Keep")
    r = client.post(f"/api/quick-video/{did}/delete", json={"confirm": True}, headers=JSON)
    assert r.status_code == 422 and db.get_drama(did) is not None


def _away(*perms, email="kid@example.com"):
    from api import auth as api_auth
    from services import auth_service
    u = auth_service.add_user(email)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}


def test_from_another_device_a_start_needs_import_from_a_link(client, monkeypatch):
    from api.api_config import ApiSettings
    from services import quick_video_service
    started = []
    monkeypatch.setattr(quick_video_service, "start",
                        lambda principal, url, *a, **kw: started.append((principal["user_id"], kw["at_pc"])) or
                        {"drama_id": 1, "watch_once": False})
    away = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                      raise_server_exceptions=False)
    body = {"url": "https://example.com/v"}
    assert away.post("/api/quick-video/start", json=body, headers=_away()).status_code == 403
    assert away.post("/api/quick-video/start", json=body,
                     headers=_away("media.import_url", email="ok@example.com")).status_code == 200
    assert len(started) == 1 and started[0][1] is False
    # Removing a watch-once title stays PC only, like every delete.
    assert away.post("/api/quick-video/1/delete", json={"confirm": True},
                     headers=_away("media.import_url", email="del@example.com")).status_code == 403


def test_from_another_device_a_member_at_the_title_limit_gets_409_and_nothing_is_made(client):
    from api.api_config import ApiSettings
    from services import auth_service, drama_service
    away = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                      raise_server_exceptions=False)
    h = _away("media.import_url", email="full@example.com")
    uid = auth_service.find_user_by_email("full@example.com")["id"]
    for n in range(drama_service.MEMBER_TITLE_LIMIT):
        db.create_drama(title_en=f"T{n}", owner_user_id=uid)
    r = away.post("/api/quick-video/start", json={"url": "https://example.com/v", "profile_id": _profile(quick_source_language="ja")},
                  headers=h)
    assert r.status_code == 409 and "200 titles" in r.json()["error"]["message"]
    assert len(db.list_dramas()) == drama_service.MEMBER_TITLE_LIMIT
