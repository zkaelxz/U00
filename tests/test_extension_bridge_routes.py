"""
The extension's device-token routes (/api/ext-bridge/*): a household
member's computer sends pages and text with its own token, under that
member's rights.
"""

import base64
import io

import pytest
from fastapi.testclient import TestClient

import db
import page_server
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import device_token_service as svc

PUBLIC_HOST = "baihe.example.com"
SIGN_IN = {"google_client_id": "cid", "google_client_secret": "s3cr3t-value",
           "public_url": f"https://{PUBLIC_HOST}"}
BASE = "/api/ext-bridge"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(svc, "_create_limiter", auth_service.SlidingWindowRateLimiter(50, 3600))
    monkeypatch.setattr(svc, "_fail_by_client", svc._FailureLimiter(20, 600))
    monkeypatch.setattr(svc, "_fail_by_prefix", svc._FailureLimiter(100, 600))
    page_server.set_translation_config(engine=None, api_key="", base_url=None, free_tier=False)


def _member(email, send=True):
    u = auth_service.add_user(email)
    if send:
        auth_service.grant_permission(u["id"], "extension.send")
    perms = auth_service.effective_permissions(u["id"])
    token = svc.create_token(dict(user_id=u["id"], is_admin=False, permissions=perms),
                             "Laptop")["token"]
    return u, token


def _client():
    app = create_app(ApiSettings(household_port=8610, serve_frontend=False, **SIGN_IN),
                     listener="household")
    return TestClient(app, base_url=f"https://{PUBLIC_HOST}", client=("198.51.100.7", 5000),
                      raise_server_exceptions=False)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _png():
    Image = pytest.importorskip("PIL.Image")
    img = Image.new("RGB", (600, 900), (240, 240, 240))
    img.paste((10, 10, 10), (150, 225, 450, 450))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _drama(owner_id, title="Mine", private=True):
    return db.create_drama(title_zh=title, title_en=title, media_type="manga",
                           owner_user_id=owner_id, is_private=int(private))


class TestAuthentication:
    def test_no_token_is_refused(self, isolated_db):
        assert _client().get(f"{BASE}/health").status_code == 401

    def test_a_session_cookie_is_not_a_token(self, isolated_db):
        u, _ = _member("a@example.com")
        s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
        r = _client().get(f"{BASE}/health", headers={
            "Cookie": f"__Host-baihe_session={s['session_token']}"})
        assert r.status_code == 401

    def test_a_member_without_the_permission_is_refused(self, isolated_db):
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "extension.send")
        assert _client().get(f"{BASE}/health", headers=_auth(token)).status_code in (401, 403)

    def test_a_bad_token_on_a_post_is_refused_before_the_body(self, isolated_db):
        r = _client().post(f"{BASE}/text", headers=_auth("baihe_dt_nope"), json={"text": "x"})
        assert r.status_code == 401


class TestHealth:
    def test_lists_only_titles_the_member_can_see(self, isolated_db):
        a, token = _member("a@example.com")
        b, _ = _member("b@example.com")
        mine = _drama(a["id"], "Mine")
        _drama(b["id"], "Theirs", private=True)
        r = _client().get(f"{BASE}/health", headers=_auth(token))
        assert r.status_code == 200
        assert [d["id"] for d in r.json()["dramas"]] == [mine]
        assert r.headers["cache-control"] == "no-store"
        assert "access-control-allow-origin" not in r.headers


class TestSending:
    def test_pages_into_another_members_title_are_a_404(self, isolated_db):
        a, token = _member("a@example.com")
        b, _ = _member("b@example.com")
        theirs = _drama(b["id"], "Theirs", private=True)
        r = _client().post(f"{BASE}/pages", headers=_auth(token), json={
            "drama_id": theirs, "images": [{"key": "k", "data": _png(),
                                            "content_type": "image/png"}]})
        assert r.status_code == 404
        assert r.json() == {"error": "no such drama"}

    def test_too_many_images_are_refused(self, isolated_db):
        _, token = _member("a@example.com")
        img = {"key": "k", "data": _png(), "content_type": "image/png"}
        r = _client().post(f"{BASE}/pages", headers=_auth(token), json={
            "images": [img] * (page_server.MAX_IMAGES_PER_REQUEST + 1)})
        assert r.status_code == 413

    def test_page_takes_exactly_one_image(self, isolated_db):
        _, token = _member("a@example.com")
        img = {"key": "k", "data": _png(), "content_type": "image/png"}
        r = _client().post(f"{BASE}/page", headers=_auth(token), json={"images": [img, img]})
        assert r.status_code == 400

    def test_an_oversized_body_is_refused_by_its_length(self, isolated_db):
        _, token = _member("a@example.com")
        r = _client().post(f"{BASE}/pages", headers={
            **_auth(token), "Content-Type": "application/json",
            "Content-Length": str(page_server.MAX_BODY_BYTES + 1)}, content=b"{}")
        assert r.status_code in (400, 413)

    def test_text_is_saved_to_the_members_own_history(self, isolated_db, monkeypatch):
        u, token = _member("a@example.com")
        page_server.set_translation_config(engine="deepl", api_key="k", base_url=None,
                                           free_tier=False)

        class Engine:
            name = "deepl"
        monkeypatch.setattr(page_server, "_build_engine", lambda config: Engine())
        from services import translate_service
        monkeypatch.setattr(translate_service, "translate_metered",
                            lambda *a, **k: ("hello", None))
        monkeypatch.setattr("translate_engines.standalone_direction_support",
                            lambda *a: (True, ""))
        r = _client().post(f"{BASE}/text", headers=_auth(token), json={
            "text": "你好", "source_language": "zh", "target_language": "en"})
        assert r.status_code == 200 and r.json()["translated_text"] == "hello"
        assert db.list_translate_history(visible_to=u["id"])

    def test_the_spending_cap_refuses_before_any_work(self, isolated_db, monkeypatch):
        _, token = _member("a@example.com")
        monkeypatch.setattr("bridge_requests.engine_name_if_ready", lambda: "claude")
        from lib.errors import UnsupportedOperationError
        from services import translate_run_service

        def spent(name, free=False):
            raise UnsupportedOperationError("This month's spending cap is used up.")
        monkeypatch.setattr(translate_run_service, "refuse_when_cap_spent", spent)
        r = _client().post(f"{BASE}/text", headers=_auth(token), json={"text": "你好"})
        assert r.status_code == 429


class TestTitleAndNovel:
    def test_auto_creates_the_title_owned_by_the_caller(self, isolated_db):
        u, token = _member("a@example.com")
        r = _client().post(f"{BASE}/title", headers=_auth(token), json={
            "url": "https://example.com/c/1", "titles": ["我的漫画 第1话 - Example"],
            "kind": "comic"})
        assert r.status_code == 200 and r.json()["created"] is True
        assert (db.get_item_ownership("drama", r.json()["drama_id"]) or {}).get(
            "owner_user_id") == u["id"]

    def test_another_members_private_title_is_never_matched(self, isolated_db):
        a, token = _member("a@example.com")
        b, _ = _member("b@example.com")
        theirs = _drama(b["id"], "我的漫画", private=True)
        db.update_drama(theirs, source_url="https://example.com/c/1")
        r = _client().post(f"{BASE}/title", headers=_auth(token), json={
            "url": "https://example.com/c/1", "titles": ["我的漫画"], "kind": "comic"})
        assert r.status_code == 200
        assert r.json()["drama_id"] != theirs

    def test_novel_text_into_another_members_title_is_refused(self, isolated_db):
        a, token = _member("a@example.com")
        b, _ = _member("b@example.com")
        theirs = db.create_drama(title_zh="n", media_type="novel", owner_user_id=b["id"],
                                 is_private=1)
        r = _client().post(f"{BASE}/novel", headers=_auth(token), json={
            "drama_id": theirs, "heading": "c1", "text": "文本"})
        assert r.status_code in (403, 404)
