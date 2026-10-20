"""/api/language-packs: listing, one pack, a title's choice, language defaults."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app


def _drama(db, lang="ja"):
    return db.create_drama(title_en="T", series_id=db.get_or_create_series("S"), source_language=lang)


def test_list_has_counts_and_no_entries(client):
    packs = client.get("/api/language-packs").json()
    ja = next(p for p in packs if p["id"] == "ja-address")
    assert ja["entry_count"] == 13 and ja["version"] == 1 and "entries" not in ja
    assert ja["styles"]["default"] == "romanised"


def test_one_pack_has_entries_and_no_paths(client):
    r = client.get("/api/language-packs/packs/zh-address")
    assert r.status_code == 200
    body = r.json()
    entry = next(e for e in body["entries"] if e["source"] == "姐姐")
    assert entry["en"] == {"natural": "big sis", "pinyin": "jiejie"}
    assert "language_pack_data" not in r.text
    assert client.get("/api/language-packs/packs/nope").status_code == 404


def test_title_choice_round_trip(client, isolated_db):
    did = _drama(isolated_db)
    url = f"/api/language-packs/dramas/{did}"
    first = client.get(url).json()
    assert first["source_language"] == "ja" and first["uses_default"] is True
    assert not any(p["enabled"] for p in first["packs"])
    r = client.post(url, json={"packs": {"ja-address": "natural"}})
    assert r.status_code == 200
    on = {p["id"]: p for p in r.json()["packs"]}
    assert on["ja-address"]["enabled"] and on["ja-address"]["style"] == "natural"
    assert r.json()["uses_default"] is False
    assert not on["ja-common"]["enabled"]


def test_bad_choices_are_refused(client, isolated_db):
    did = _drama(isolated_db)
    url = f"/api/language-packs/dramas/{did}"
    assert client.post(url, json={"packs": {"ko-address": None}}).status_code == 422
    assert client.post(url, json={"packs": {"ja-address": "zzz"}}).status_code == 422
    assert client.post("/api/language-packs/dramas/9999", json={"packs": {}}).status_code == 404


def test_language_default(client, isolated_db):
    did = _drama(isolated_db, "ko")
    r = client.post("/api/language-packs/defaults/ko", json={"packs": {"ko-address": None}})
    assert r.status_code == 200 and r.json() == {"language": "ko", "packs": ["ko-address"]}
    mine = client.get(f"/api/language-packs/dramas/{did}").json()
    assert mine["uses_default"] and next(p for p in mine["packs"] if p["id"] == "ko-address")["enabled"]
    assert client.post("/api/language-packs/defaults/fr", json={"packs": {}}).status_code == 422


def test_language_default_is_readable(client, isolated_db):
    assert client.get("/api/language-packs/defaults/ko").json() == {"language": "ko", "packs": []}
    client.post("/api/language-packs/defaults/ko", json={"packs": {"ko-address": None}})
    assert client.get("/api/language-packs/defaults/ko").json() == {"language": "ko", "packs": ["ko-address"]}
    assert client.get("/api/language-packs/defaults/fr").status_code == 422


def test_language_default_write_is_pc_only(isolated_db):
    """An app-wide settings write: refused to any caller not at the PC, even an admin."""
    from api import auth as api_auth
    from services import auth_service
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
    url = "/api/language-packs/defaults/ko"
    body = {"packs": {"ko-address": None}}
    for user in (auth_service.add_user("kid@example.com"),
                 auth_service.grant_admin_local("admin@example.com")):
        s = auth_service.create_session(user["id"])
        headers = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                   api_auth.CSRF_HEADER: s["csrf_token"]}
        assert remote.post(url, json=body, headers=headers).status_code == 403
        assert remote.get(url, headers=headers).status_code == 200
    local = TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
    assert local.post(url, json=body).status_code == 200
