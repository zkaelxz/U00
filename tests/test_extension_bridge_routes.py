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


def _member(email, send=True, paid=False):
    u = auth_service.add_user(email)
    if send:
        auth_service.grant_permission(u["id"], "extension.send")
    if paid:
        auth_service.grant_permission(u["id"], "engines.paid")
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
        assert _client().get(f"{BASE}/health", headers=_auth(token)).status_code == 401

    def test_a_bad_token_on_a_post_is_refused_before_the_body(self, isolated_db):
        r = _client().post(f"{BASE}/text", headers=_auth("baihe_dt_nope"), json={"text": "x"})
        assert r.status_code == 401


class TestTheGateExemptionIsExact:
    def test_a_longer_path_still_needs_a_session(self, isolated_db):
        _, token = _member("a@example.com")
        r = _client().get(f"{BASE}/health/extra", headers=_auth(token))
        assert r.status_code == 401

    def test_other_api_paths_do_not_take_the_token(self, isolated_db):
        _, token = _member("a@example.com")
        assert _client().get("/api/library/dramas", headers=_auth(token)).status_code == 401


class TestHealth:
    def test_lists_only_titles_the_member_can_see(self, isolated_db):
        a, token = _member("a@example.com")
        b, _ = _member("b@example.com")
        mine = _drama(a["id"], "Mine")
        _drama(b["id"], "Theirs", private=True)
        r = _client().get(f"{BASE}/health", headers=_auth(token))
        assert r.status_code == 200
        assert [d["id"] for d in r.json()["dramas"]] == [mine]
        assert "app_port" not in r.json()
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

    def test_an_out_of_range_title_id_is_a_404(self, isolated_db):
        _, token = _member("a@example.com")
        img = {"key": "k", "data": _png(), "content_type": "image/png"}
        for route in ("page", "pages"):
            r = _client().post(f"{BASE}/{route}", headers=_auth(token),
                               json={"drama_id": 2 ** 63, "images": [img]})
            assert (r.status_code, r.json()) == (404, {"error": "no such drama"}), route
        import json
        r = _client().post(f"{BASE}/page", headers={**_auth(token), "Content-Type": "application/json"},
                           content=json.dumps({"images": [img]})[:-1] + ', "drama_id": 1e999}')
        assert (r.status_code, r.json()) == (404, {"error": "no such drama"})

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
        assert r.status_code == 413

    def test_text_is_saved_to_the_members_own_history(self, isolated_db, monkeypatch):
        u, token = _member("a@example.com", paid=True)
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
        _, token = _member("a@example.com", paid=True)
        monkeypatch.setattr("services.extension_bridge_service.engine_name_if_ready", lambda: "claude")
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

    def test_a_private_title_made_at_the_pc_is_never_matched(self, isolated_db):
        """It has no account owner; a match would answer 404 where an unknown
        link answers 422, telling the member the private title exists."""
        _a, token = _member("a@example.com")
        pc = _drama(None, "我的漫画", private=True)
        db.update_drama(pc, source_url="https://example.com/c/1")
        find = {"titles": ["我的漫画"], "kind": "comic", "create": False}
        known = _client().post(f"{BASE}/title", headers=_auth(token),
                               json={"url": "https://example.com/c/1", **find})
        unknown = _client().post(f"{BASE}/title", headers=_auth(token),
                                 json={"url": "https://example.com/c/2", **find})
        assert (known.status_code, known.json()) == (unknown.status_code, unknown.json()) \
            and known.status_code == 422
        r = _client().post(f"{BASE}/title", headers=_auth(token), json={
            "url": "https://example.com/c/1", "titles": ["我的漫画"], "kind": "comic"})
        assert r.status_code == 200 and r.json()["created"] is True
        assert r.json()["drama_id"] != pc

    def test_novel_text_into_another_members_title_is_refused(self, isolated_db):
        a, token = _member("a@example.com")
        b, _ = _member("b@example.com")
        theirs = db.create_drama(title_zh="n", media_type="novel", owner_user_id=b["id"],
                                 is_private=1)
        r = _client().post(f"{BASE}/novel", headers=_auth(token), json={
            "drama_id": theirs, "heading": "c1", "text": "文本"})
        assert r.status_code == 404


class _PaidEngine:
    name = "claude"
    model = "claude-x"


class TestPaidEngines:
    @pytest.fixture
    def paid_engine(self, monkeypatch):
        page_server.set_translation_config(engine="claude", api_key="k", base_url=None,
                                           free_tier=False)
        monkeypatch.setattr(page_server, "_build_engine", lambda config: _PaidEngine())

    def test_a_member_without_engines_paid_cannot_use_a_paid_engine(self, isolated_db, paid_engine):
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "engines.paid")
        for path, body in (("text", {"text": "你好"}),
                           ("page", {"images": [{"key": "k", "data": _png()}]})):
            r = _client().post(f"{BASE}/{path}", headers=_auth(token), json=body)
            assert r.status_code == 403, path

    def test_engines_paid_lets_the_text_through(self, isolated_db, paid_engine, monkeypatch):
        u, token = _member("a@example.com")
        auth_service.grant_permission(u["id"], "engines.paid")
        from services import translate_service
        monkeypatch.setattr(translate_service, "translate_metered", lambda *a, **k: ("hi", None))
        monkeypatch.setattr("translate_engines.standalone_direction_support",
                            lambda *a: (True, ""))
        r = _client().post(f"{BASE}/text", headers=_auth(token), json={"text": "你好"})
        assert r.status_code == 200

    def test_a_free_engine_needs_no_engines_paid(self, isolated_db, monkeypatch):
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "engines.paid")
        import translate_engines
        free = sorted(translate_engines.FREE_ENGINES)[0]
        page_server.set_translation_config(engine=free, api_key="k", base_url=None,
                                           free_tier=False)

        class Engine:
            name = free
        monkeypatch.setattr(page_server, "_build_engine", lambda config: Engine())
        from services import translate_service
        monkeypatch.setattr(translate_service, "translate_metered", lambda *a, **k: ("hi", None))
        monkeypatch.setattr("translate_engines.standalone_direction_support",
                            lambda *a: (True, ""))
        r = _client().post(f"{BASE}/text", headers=_auth(token), json={"text": "你好"})
        assert r.status_code == 200


class TestMeteringAndMessages:
    def test_a_page_sent_with_no_title_is_still_logged_for_the_cap(self, isolated_db):
        log = page_server._usage_cb(None, {"engine": "claude"}, _PaidEngine())
        log(1000, 500)
        with db.get_conn() as conn:
            rows = conn.execute("SELECT drama_id, engine, operation FROM usage_log").fetchall()
        assert [tuple(r) for r in rows] == [(None, "claude", "extension_translate")]

    def test_a_members_text_failure_note_carries_no_engine_detail(self, isolated_db, monkeypatch):
        page_server.set_translation_config(engine="claude", api_key="k", base_url=None,
                                           free_tier=False)
        monkeypatch.setattr(page_server, "_build_engine", lambda config: _PaidEngine())
        monkeypatch.setattr("translate_engines.standalone_direction_support",
                            lambda *a: (True, ""))
        from services import translate_service

        def boom(*a, **k):
            raise RuntimeError("connect to http://10.0.0.5:11434 failed in C:\\Users\\kae")
        monkeypatch.setattr(translate_service, "translate_metered", boom)
        member = page_server.translate_text_block("你好", "zh", "en", member=True)
        owner = page_server.translate_text_block("你好", "zh", "en")
        assert "10.0.0.5" not in str(member["notes"]) and "kae" not in str(member["notes"])
        assert "10.0.0.5" in str(owner["notes"])
