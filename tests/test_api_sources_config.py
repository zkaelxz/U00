"""Tests for /api/sources config writes (Migration Slice 56, S-2): mocked, no
network, isolated library (and therefore isolated sources.db)."""

import json
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app
from sources import cache as src_cache
from sources import health, profiles, store
from sources import http as src_http


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def pacing_resets(monkeypatch):
    calls = []
    monkeypatch.setattr(src_http, "reset_pacing_state", lambda: calls.append(1))
    return calls


def _err(r):
    return r.json()["error"]["code"]


def test_enabled_toggle_and_unknown_404(client):
    r = client.post("/api/sources/manhuagui/enabled", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert store.get_setting("disabled_sources") == ["manhuagui"]
    assert client.post("/api/sources/manhuagui/enabled", json={"enabled": True}).json()["enabled"]
    r = client.post("/api/sources/nope/enabled", json={"enabled": True})
    assert r.status_code == 404
    assert client.post("/api/sources/manhuagui/enabled", json={"enabled": "yes"}).status_code == 422


def test_adult_toggle(client):
    r = client.post("/api/sources/manhuagui/adult", json={"enabled": True})
    assert r.status_code == 200 and r.json()["adult_enabled"] is True
    assert store.adult_enabled("manhuagui")
    r = client.post("/api/sources/xbanxia/adult", json={"enabled": True})
    assert r.status_code == 400 and _err(r) == "unsupported_operation"
    assert not store.adult_enabled("xbanxia")
    assert client.post("/api/sources/nope/adult", json={"enabled": True}).status_code == 404


def test_settings_write_saves_and_resets_pacing(client, pacing_resets):
    r = client.post("/api/sources/settings", json={
        "pace_min_delay": 5, "pace_max_delay": 2, "max_retries": 2, "cache_mode": "none",
        "auto_queue_new_chapters": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pace_min_delay"] == 5.0
    assert body["pace_max_delay"] == 5.0          # a max is never below its min
    assert body["cache_mode"] == "none" and body["auto_queue_new_chapters"] is True
    assert store.get_setting("max_retries") == 2
    assert pacing_resets == [1]
    assert body["proxy_configured"] is False


def test_settings_rejects_proxy_and_unknown_keys(client, pacing_resets):
    for key, val in (("http_proxy_url", "http://10.0.0.1:1"), ("proxy_url", "http://x"),
                     ("page_server_enabled", True), ("disabled_sources", [])):
        r = client.post("/api/sources/settings", json={key: val})
        assert r.status_code == 422, key
        assert _err(r) == "validation_error"
        assert "10.0.0.1" not in r.text
    assert store.get_setting("http_proxy_url") == ""
    assert pacing_resets == []


def test_settings_service_rejects_proxy_key_too(isolated_db):
    from services import sources_registry_service as svc
    from services.service_errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        svc.update_settings({"http_proxy_url": "http://x"})


def test_pacing_floor_enforced(client, pacing_resets):
    r = client.post("/api/sources/settings", json={"pace_min_delay": 0})
    assert r.status_code == 422
    r = client.post("/api/sources/settings", json={"pace_min_delay": 2.9})
    assert r.status_code == 422
    assert client.post("/api/sources/settings", json={"pace_min_delay": 3.0}).status_code == 200
    assert store.get_setting("pace_min_delay") == 3.0


def test_settings_ranges_and_enum(client, pacing_resets):
    for body in ({"max_concurrent": 0}, {"max_concurrent": 5}, {"max_retries": 7},
                 {"pace_max_delay": 121}, {"session_break_max_delay": 901},
                 {"check_interval_hours": 169}, {"cache_mode": "everything"},
                 {"pace_min_delay": None}, {"auto_queue_new_chapters": "true"}, {}):
        assert client.post("/api/sources/settings", json=body).status_code == 422, body
    assert pacing_resets == []
    assert store.get_setting("max_concurrent") == 1


def test_cache_clear_needs_confirm(client):
    rc = src_cache.RawCache(mode="keep_originals")
    rc.put("https://h.example/x.png", b"abc")
    assert rc.stats()["entries"] == 1
    assert client.post("/api/sources/cache/clear", json={}).status_code == 422
    assert client.post("/api/sources/cache/clear", json={"confirm": "true"}).status_code == 422
    assert rc.stats()["entries"] == 1
    r = client.post("/api/sources/cache/clear", json={"confirm": True})
    assert r.status_code == 200 and r.json() == {"entries": 0, "bytes": 0}


def test_health_reset(client):
    for _ in range(3):
        health.record_failure("manhuagui", "ConnectError", "boom")
    assert health.retry_after("manhuagui")
    r = client.post("/api/sources/manhuagui/health/reset")
    assert r.status_code == 200
    assert r.json()["light"] == "green" and r.json()["retry_after"] is None
    assert client.post("/api/sources/nope/health/reset").status_code == 404


def _profile(domain="a.example"):
    os.makedirs(profiles.profiles_dir(), exist_ok=True)
    rec = {"domain": domain, "format": 1, "active": {"novel": 2}, "versions": [
        {"version": 1, "kind": "novel", "status": "superseded", "origin": "auto", "rules": {},
         "created_at": 1.0},
        {"version": 2, "kind": "novel", "status": "active", "origin": "auto", "rules": {},
         "created_at": 2.0}]}
    with open(os.path.join(profiles.profiles_dir(), f"{domain}.json"), "w") as f:
        json.dump(rec, f)


def test_profile_rollback(client, isolated_db):
    _profile()
    r = client.post("/api/sources/profiles/a.example/novel/rollback", json={"version": 1})
    assert r.status_code == 200
    assert {v["version"]: v["status"] for v in r.json()} == {1: "active", 2: "superseded"}


def test_profile_rollback_rejected_version_404(client, isolated_db):
    _profile()
    r = client.post("/api/sources/profiles/a.example/novel/rollback", json={"version": 9})
    assert r.status_code == 404 and _err(r) == "not_found"
    r = client.post("/api/sources/profiles/a.example/comic/rollback", json={"version": 1})
    assert r.status_code == 404
    r = client.post("/api/sources/profiles/none.example/novel/rollback", json={"version": 1})
    assert r.status_code == 404
    assert client.post("/api/sources/profiles/a.example/novel/rollback",
                       json={"version": 0}).status_code == 422


def test_dismiss_notification(client):
    store.record_new_chapters("manhuagui", "1", [SimpleNamespace(chapter_id="c1", title="Ch 1")])
    nid = store.list_notifications()[0]["id"]
    r = client.post(f"/api/sources/notifications/{nid}/dismiss")
    assert r.status_code == 200 and r.json()["dismissed"] is True
    assert client.get("/api/sources/notifications").json() == []
    assert client.post("/api/sources/notifications/9999/dismiss").status_code == 404


def test_track_is_refused_but_untrack_works(client, isolated_db):
    import db
    from sources import store
    did = db.create_drama(title_en="D")
    body = {"source": "manhuagui", "series_id": "77", "title": "T",
            "url": "https://www.manhuagui.com/comic/77/", "drama_id": did}
    # tracking a new series would announce the whole back catalogue: refused
    r = client.post("/api/sources/tracked", json=body)
    assert r.status_code == 400
    assert client.get("/api/sources/tracked").json() == []
    assert client.post("/api/sources/tracked", json={**body, "source": "nope"}).status_code == 404
    # a series tracked elsewhere (Streamlit) can still be untracked
    store.track_series("manhuagui", "77", "T", url=body["url"], drama_id=did)
    r = client.post("/api/sources/tracked", json={"source": "manhuagui", "series_id": "77",
                                                  "tracked": False})
    assert r.status_code == 200 and r.json() == []
    r = client.post("/api/sources/tracked", json={"source": "manhuagui", "series_id": "77",
                                                  "tracked": False})
    assert r.status_code == 404


def test_writes_are_post_only(client):
    for path in ("/api/sources/manhuagui/enabled", "/api/sources/settings",
                 "/api/sources/cache/clear", "/api/sources/tracked"):
        assert client.put(path, json={}).status_code == 405
        assert client.delete(path).status_code == 405
