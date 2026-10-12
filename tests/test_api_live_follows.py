"""Followed-channel routes (api/routers/live_follow_routes.py): permissions,
ownership and what a response may carry. No network: DNS is faked and no
check runs (the poller thread is not started by these apps)."""
import json
import socket

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, live_follow_poller, live_service, notification_service
from sources import live_follow_store

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443))]


class _Thread:
    def is_alive(self):
        return True


@pytest.fixture
def env(isolated_db, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    live_follow_poller.reset_for_tests()
    monkeypatch.setattr(live_follow_poller, "_spawn", lambda target, name: _Thread())
    monkeypatch.setattr(live_follow_poller, "_thread", None)
    monkeypatch.setattr(live_follow_poller, "_worker", None)
    starts = []
    monkeypatch.setattr(live_service, "start_session",
                        lambda url, **kw: starts.append(dict(kw, url=url)) or {"session_id": "live_" + "a" * 32})
    yield starts
    live_follow_poller.reset_for_tests()


def _on_client():
    return TestClient(create_app(ApiSettings(auth_mode="on")),
                      base_url="https://baihe.example.com", raise_server_exceptions=False)


def _headers(*perms, email="kid@example.com", revoke=()):
    u = auth_service.add_user(email)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    for p in revoke:
        auth_service.revoke_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_permissions_and_ownership_with_auth_on(env):
    c = _on_client()
    body = {"channel": "@alice_ch"}
    assert c.post("/api/live/follows", json=body).status_code == 401
    household = _headers()
    assert c.post("/api/live/follows", json=body, headers=household).status_code == 403
    assert c.get("/api/live/follows", headers=household).json()["items"] == []
    alice = _headers("media.import_url", email="alice@example.com")
    bob = _headers("media.import_url", email="bob@example.com")
    r = c.post("/api/live/follows", json=body, headers=alice)
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert "youtube.com" not in r.text and "http" not in r.text
    # Auto-start is the PC owner's only.
    r = c.post("/api/live/follows", json={"channel": "@x_ch", "mode": "auto"}, headers=alice)
    assert r.status_code == 403
    assert c.post(f"/api/live/follows/{fid}", json={"mode": "auto"}, headers=alice).status_code == 403
    assert c.get("/api/live/follows", headers=alice).json()["can_auto_start"] is False
    # Another member: 404 on every follow route, and nothing listed.
    assert c.get("/api/live/follows", headers=bob).json()["items"] == []
    for path in (f"/api/live/follows/{fid}/check", f"/api/live/follows/{fid}/remove",
                 f"/api/live/follows/{fid}/start"):
        assert c.post(path, headers=bob).status_code == 404, path
    assert c.post(f"/api/live/follows/{fid}", json={"options": {"whisper_size": "tiny"}},
                  headers=bob).status_code == 404
    assert live_follow_store.get(fid) is not None
    # A member without import_url can't change or check follows.
    assert c.post(f"/api/live/follows/{fid}/check", headers=household).status_code == 403
    no_read = _headers(email="nr@example.com", revoke=("library.read",))
    assert c.get("/api/live/follows", headers=no_read).status_code == 403
    assert c.post(f"/api/live/follows/{fid}/check", headers=alice).json() == {"id": fid, "queued": True}
    # Check all: only the caller's own channels.
    bob_fid = c.post("/api/live/follows", json={"channel": "@bob_ch"}, headers=bob).json()["id"]
    live_follow_poller.reset_for_tests()
    assert c.post("/api/live/follows/check-all", headers=alice).json() == {"queued": 1, "skipped": 0}
    assert live_follow_poller._requested == [fid]
    assert c.post("/api/live/follows/check-all", headers=household).status_code == 403
    assert bob_fid not in live_follow_poller._requested
    assert c.post(f"/api/live/follows/{fid}/remove", headers=alice).json()["removed"] is True


def test_start_live_needs_the_live_start_permissions(env):
    c = _on_client()
    alice = _headers("media.import_url", email="alice@example.com")
    fid = c.post("/api/live/follows", json={"channel": "@alice_ch"}, headers=alice).json()["id"]
    # Free default engine: a normal Live start, as Alice.
    r = c.post(f"/api/live/follows/{fid}/start", headers=alice)
    assert r.status_code == 200 and env[0]["url"] == "https://www.youtube.com/@alice_ch/live"
    assert env[0]["principal"]["user_id"] is not None and env[0]["use_saved_cookies"] is False
    # Saving a paid engine needs engines.paid, like a Live start does.
    r = c.post(f"/api/live/follows/{fid}", json={"options": {"engine": "claude"}}, headers=alice)
    assert r.status_code == 403
    r = c.post(f"/api/live/follows/{fid}", json={"options": {"model": "gpt-oss:120b-cloud"}},
               headers=alice)
    assert r.status_code == 403
    # A paid engine saved while she held engines.paid is re-checked at start.
    live_follow_store.update(fid, options=json.dumps({"engine": "claude"}))
    assert c.post(f"/api/live/follows/{fid}/start", headers=alice).status_code == 403
    assert len(env) == 1


def test_auth_off_owner_flow_and_no_url_in_responses(env):
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                   headers={"X-Baihe-Local": "1"})
    assert c.post("/api/live/follows", json={
        "channel": "https://www.youtube.com/@owner_ch", "mode": "auto"}).status_code == 422
    r = c.post("/api/live/follows", json={
        "channel": "https://www.youtube.com/@owner_ch", "mode": "auto", "auto_check": True,
        "options": {"engine": "fake", "max_minutes": 5}})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert r.json()["mode"] == "auto" and r.json()["options"]["max_minutes"] == 5
    listed = c.get("/api/live/follows")
    assert listed.json()["can_auto_start"] is True
    assert "youtube.com" not in listed.text and "http" not in listed.text
    for bad in ({"channel": "https://evil.example/@x"}, {"channel": "@x_ch", "mode": "loud"},
                {"channel": "@x_ch", "url": "https://x"},
                {"channel": "@x_ch", "options": {"max_minutes": 0}},
                {"channel": "@x_ch", "options": {"cookies": "x"}}):
        assert c.post("/api/live/follows", json=bad).status_code == 422, bad
    assert c.post("/api/live/follows/0/check").status_code == 422
    assert c.post("/api/live/follows/999/check").status_code == 404
    r = c.post(f"/api/live/follows/{fid}/start")
    assert r.status_code == 200 and env[0]["use_saved_cookies"] is True
    assert c.post(f"/api/live/follows/{fid}/check").status_code == 200
    # Still queued: a second press changes nothing.
    assert c.post(f"/api/live/follows/{fid}/check").json() == {"id": fid, "queued": True}
    live_follow_poller.reset_for_tests()
    live_follow_store.update(fid, last_checked_at=__import__("time").time())
    assert c.post(f"/api/live/follows/{fid}/check").status_code == 429


def test_the_bell_carries_the_start_action_for_the_follows_owner_only(env):
    notification_service.reset_for_tests()
    c = _on_client()
    alice = _headers("media.import_url", email="alice@example.com")
    bob = _headers(email="bob@example.com")
    alice_id = auth_service.find_user_by_email("alice@example.com")["id"]
    notification_service.notify_live_follow("Ch is live now", alice_id, 7, start_action=True)
    notification_service.notify_live_follow("Ch scheduled a stream", alice_id, 7,
                                            starts_at=1800000000.0)
    items = c.get("/api/notifications", headers=alice).json()["items"]
    assert [i["kind"] for i in items] == ["live", "live"]
    assert items[0]["starts_at"] == 1800000000.0 and items[0]["action"] is None
    assert items[1]["action"] == "start_live" and items[1]["follow_id"] == 7
    assert c.get("/api/notifications", headers=bob).json()["items"] == []
