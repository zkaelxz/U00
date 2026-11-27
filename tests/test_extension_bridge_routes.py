"""
The extension's device-token routes (/api/ext-bridge/*): a household
member's computer sends pages and text with its own token, under that
member's rights.
"""

import base64
import functools
import io

import pytest
from fastapi.testclient import TestClient

import db
from services import extension_bridge_service, page_server
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
        monkeypatch.setattr("engine_backends.standalone.standalone_direction_support",
                            lambda *a: (True, ""))
        r = _client().post(f"{BASE}/text", headers=_auth(token), json={
            "text": "你好", "source_language": "zh", "target_language": "en"})
        assert r.status_code == 200 and r.json()["translated_text"] == "hello"
        assert db.list_translate_history(visible_to=u["id"])

    def test_a_no_save_text_read_again_comes_from_the_cache_without_the_engine(self, isolated_db, monkeypatch):
        _, token = _member("a@example.com", paid=True)
        page_server.set_translation_config(engine="deepl", api_key="k", base_url=None,
                                           free_tier=False)

        class Engine:
            name = "deepl"
        monkeypatch.setattr(page_server, "_build_engine", lambda config: Engine())
        from services import translate_service
        calls = []
        monkeypatch.setattr(translate_service, "translate_metered",
                            lambda *a, **k: (calls.append(1) or "hello", None))
        monkeypatch.setattr("engine_backends.standalone.standalone_direction_support",
                            lambda *a: (True, ""))
        body = {"text": "你好", "source_language": "zh", "target_language": "en", "store": False}
        first = _client().post(f"{BASE}/text", headers=_auth(token), json=body)
        again = _client().post(f"{BASE}/text", headers=_auth(token), json=body)
        assert first.json()["translated_text"] == again.json()["translated_text"] == "hello"
        assert len(calls) == 1 and again.json()["cached"] is True
        # A saved send is never answered from it: its history row has to be written.
        _client().post(f"{BASE}/text", headers=_auth(token), json={**body, "store": True})
        assert len(calls) == 2

    def test_the_spending_cap_refuses_before_any_work(self, isolated_db, monkeypatch):
        _, token = _member("a@example.com", paid=True)
        monkeypatch.setattr("services.extension_bridge_service.engine_name_if_ready", lambda: "claude")
        from lib.errors import UnsupportedOperationError
        from services import translate_run_service

        def spent(name, free=False, model=None):
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


class TestWritePermissions:
    """`extension.send` alone must not write into titles once `lines.edit` or `jobs.start` is gone."""

    ROUTES = {
        "novel": {"heading": "c1", "text": "文本"},
        "title": {"url": "https://example.com/c/1", "titles": ["我的漫画"], "kind": "comic"},
        "text": {"text": "你好"},
        "page": {"images": ["x"]},
        "pages": {"images": ["x"]},
    }

    @pytest.mark.parametrize("route", sorted(ROUTES))
    def test_without_lines_edit_every_write_route_is_refused(self, isolated_db, route):
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "lines.edit")
        r = _client().post(f"{BASE}/{route}", headers=_auth(token), json=self.ROUTES[route])
        assert r.status_code == 403

    @pytest.mark.parametrize("route", ["text", "page", "pages"])
    def test_without_jobs_start_the_engine_routes_are_refused(self, isolated_db, route):
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "jobs.start")
        r = _client().post(f"{BASE}/{route}", headers=_auth(token), json=self.ROUTES[route])
        assert r.status_code == 403

    def test_a_refused_title_creates_nothing(self, isolated_db):
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "lines.edit")
        before = len(db.list_dramas())
        _client().post(f"{BASE}/title", headers=_auth(token), json=self.ROUTES["title"])
        assert len(db.list_dramas()) == before

    def test_health_and_cancel_stay_open(self, isolated_db):
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "lines.edit")
        auth_service.revoke_permission(u["id"], "jobs.start")
        assert _client().get(f"{BASE}/health", headers=_auth(token)).status_code == 200
        assert _client().post(f"{BASE}/cancel", headers=_auth(token), json={}).status_code != 403

    def test_a_member_with_the_default_rights_still_works(self, isolated_db):
        _, token = _member("a@example.com")
        r = _client().post(f"{BASE}/title", headers=_auth(token), json=self.ROUTES["title"])
        assert r.status_code == 200


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
        monkeypatch.setattr("engine_backends.standalone.standalone_direction_support",
                            lambda *a: (True, ""))
        r = _client().post(f"{BASE}/text", headers=_auth(token), json={"text": "你好"})
        assert r.status_code == 200

    def test_a_free_engine_needs_no_engines_paid(self, isolated_db, monkeypatch):
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "engines.paid")
        from engine_backends import engine_registry
        free = sorted(engine_registry.FREE_ENGINES)[0]
        page_server.set_translation_config(engine=free, api_key="k", base_url=None,
                                           free_tier=False)

        class Engine:
            name = free
        monkeypatch.setattr(page_server, "_build_engine", lambda config: Engine())
        from services import translate_service
        monkeypatch.setattr(translate_service, "translate_metered", lambda *a, **k: ("hi", None))
        monkeypatch.setattr("engine_backends.standalone.standalone_direction_support",
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
        monkeypatch.setattr("engine_backends.standalone.standalone_direction_support",
                            lambda *a: (True, ""))
        from services import translate_service

        def boom(*a, **k):
            raise RuntimeError("connect to http://10.0.0.5:11434 failed in C:\\Users\\alex")
        monkeypatch.setattr(translate_service, "translate_metered", boom)
        member = page_server.translate_text_block("你好", "zh", "en", member=True)
        owner = page_server.translate_text_block("你好", "zh", "en")
        assert "10.0.0.5" not in str(member["notes"]) and "alex" not in str(member["notes"])
        assert "10.0.0.5" in str(owner["notes"])


def test_the_pcs_own_token_gets_a_404_for_an_out_of_range_title_id(isolated_db):
    """With the PC's own token there is no member, so require_drama_access
    lets every id through and the page pipeline looks the title up itself."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), (255, 255, 255)).save(buf, "PNG")
    for bad in (2 ** 63, float("inf")):
        with pytest.raises(page_server.EndpointError) as exc:
            page_server.translate_image(buf.getvalue(), "image/png", drama_id=bad)
        assert (exc.value.status, str(exc.value)) == (404, "no such drama"), bad


def test_health_reports_the_shipped_extension_version():
    import json
    from services import extension_bridge_service as svc
    with open(svc._MANIFEST, encoding="utf-8") as f:
        assert svc.bundled_extension_version() == json.load(f)["version"]


class TestTitleNaming:
    """A new title's English name is one AI call on the owner's extension engine."""

    def _setup(self, monkeypatch, engine):
        from engine_backends import engine_registry, llm_tasks
        from services import translate_run_service
        calls = []
        page_server.set_translation_config(engine=engine, api_key="k", base_url=None, free_tier=False)
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: object())
        monkeypatch.setattr(translate_run_service, "refuse_when_cap_spent", lambda *a, **k: None)
        monkeypatch.setattr(llm_tasks, "call_llm_json",
                            lambda e, prompt, **kw: calls.append(prompt) or '{"title": "Hidden Child"}')
        return calls

    def _title(self, token):
        body = {"url": "https://comic.naver.com/webtoon/detail?titleId=1&no=1",
                "titles": ["남편이 아이를 숨겼다 1화 - 네이버 웹툰", "네이버 웹툰"]}
        return _client().post(f"{BASE}/title", json=body, headers=_auth(token))

    def test_a_member_without_engines_paid_gets_the_title_but_no_paid_call(self, isolated_db, monkeypatch):
        calls = self._setup(monkeypatch, "claude")
        _u, token = _member("a@example.com")
        r = self._title(token)
        assert r.status_code == 200 and calls == []
        assert db.get_drama(r.json()["drama_id"])["title_en"] == ""

    def test_a_member_with_engines_paid_gets_the_name(self, isolated_db, monkeypatch):
        calls = self._setup(monkeypatch, "claude")
        _u, token = _member("a@example.com", paid=True)
        r = self._title(token)
        assert len(calls) == 1
        assert db.get_drama(r.json()["drama_id"])["title_en"] == "Hidden Child"

    def test_a_free_local_engine_is_open_to_every_member(self, isolated_db, monkeypatch):
        calls = self._setup(monkeypatch, "ollama")
        _u, token = _member("a@example.com")
        assert self._title(token).status_code == 200 and len(calls) == 1

    def test_a_member_without_jobs_start_gets_the_title_but_no_call(self, isolated_db, monkeypatch):
        calls = self._setup(monkeypatch, "ollama")
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "jobs.start")
        r = self._title(token)
        assert r.status_code == 200 and calls == []
        assert db.get_drama(r.json()["drama_id"])["title_en"] == ""

class TestCaptureSummary:
    def test_a_member_logs_a_summary_for_a_title_they_can_edit(self, isolated_db, monkeypatch):
        import applog
        lines = []
        monkeypatch.setattr(applog, "get_logger", lambda: type("L", (), {"info": staticmethod(
            lambda fmt, *a: lines.append(fmt % a))})())
        u, token = _member("a@example.com")
        did = _drama(u["id"])
        r = _client().post(f"{BASE}/capture-summary", json={"drama_id": did, "summary": "Saved 2 of 2 pages"},
                           headers=_auth(token))
        assert r.status_code == 200 and lines == [f"Extension capture (title {did}, member): Saved 2 of 2 pages"]

    def test_a_member_cannot_log_against_someone_elses_private_title(self, isolated_db, monkeypatch):
        import applog
        lines = []
        monkeypatch.setattr(applog, "get_logger", lambda: type("L", (), {"info": staticmethod(
            lambda fmt, *a: lines.append(fmt % a))})())
        owner, _t = _member("owner@example.com")
        _u, token = _member("a@example.com")
        r = _client().post(f"{BASE}/capture-summary", json={"drama_id": _drama(owner["id"]), "summary": "x"},
                           headers=_auth(token))
        assert r.status_code == 404 and lines == []

    def test_without_lines_edit_no_note_is_kept(self, isolated_db):
        u, token = _member("a@example.com")
        did = _drama(u["id"])
        body = {"drama_id": did, "summary": "Saved 2 of 2 pages"}
        auth_service.revoke_permission(u["id"], "lines.edit")
        assert _client().post(f"{BASE}/capture-summary", json=body, headers=_auth(token)).status_code == 403
        assert db.list_capture_notes(did) == []
        auth_service.grant_permission(u["id"], "lines.edit")
        assert _client().post(f"{BASE}/capture-summary", json=body, headers=_auth(token)).status_code == 200
        assert [n["summary"] for n in db.list_capture_notes(did)] == ["Saved 2 of 2 pages"]

    def test_no_token_is_refused(self, isolated_db):
        assert _client().post(f"{BASE}/capture-summary", json={"summary": "x"}).status_code == 401


class TestPageReport:
    def test_a_device_token_can_send_a_page_report(self, isolated_db):
        import base64
        from services import bug_report_service
        _, token = _member("a@example.com")
        payload = {"image": "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + (7).to_bytes(2, "big") + b"JFIF\x00\xff\xda\x00\x02scan\xff\xd9").decode(),
                   "note": "wrong text", "regions": []}
        r = _client().post(f"{BASE}/report", headers=_auth(token), json=payload)
        assert r.status_code == 200, r.text
        assert bug_report_service.list_reports()[0]["id"] == r.json()["id"]

    def test_a_phone_app_report_needs_no_picture_and_is_scrubbed(self, isolated_db):
        from services import bug_report_service
        _, token = _member("a@example.com")
        payload = {"kind": "app", "note": "Grab froze on 192.168.1.9", "screen": "Grab",
                   "page_url": "https://example.com/ch1?token=abc", "phone": "Build 12, Android 34",
                   "crash": "java.lang.IllegalStateException: boom at 10.0.0.7"}
        r = _client().post(f"{BASE}/report", headers=_auth(token), json=payload)
        assert r.status_code == 200, r.text
        md = bug_report_service.get_report(r.json()["id"])["markdown"]
        assert "Build 12" in md and "IllegalStateException" in md and "Screenshot: not included" in md
        assert "192.168.1.9" not in md and "10.0.0.7" not in md and "token=abc" not in md
        bad = dict(payload, image="data:image/png;base64,AAAA")
        assert _client().post(f"{BASE}/report", headers=_auth(token), json=bad).status_code == 400

    def test_reports_need_library_read_and_are_rate_limited(self, isolated_db, monkeypatch):
        import base64
        from services import extension_bridge_service
        _, token = _member("a@example.com")
        payload = {"image": "data:image/jpeg;base64," + base64.b64encode(
            b"\xff\xd8\xff\xe0" + (7).to_bytes(2, "big") + b"JFIF\x00\xff\xda\x00\x02scan\xff\xd9").decode()}
        monkeypatch.setattr(extension_bridge_service, "_page_report_limiter",
                            auth_service.SlidingWindowRateLimiter(1, 600))
        assert _client().post(f"{BASE}/report", headers=_auth(token), json=payload).status_code == 200
        assert _client().post(f"{BASE}/report", headers=_auth(token), json=payload).status_code == 429
        u, other = _member("b@example.com")
        auth_service.revoke_permission(u["id"], "library.read")
        assert _client().post(f"{BASE}/report", headers=_auth(other), json=payload).status_code == 403

    def test_a_report_without_a_picture_is_refused_and_a_bad_token_is_too(self, isolated_db):
        _, token = _member("a@example.com")
        assert _client().post(f"{BASE}/report", headers=_auth(token), json={"note": "x"}).status_code == 400
        assert _client().post(f"{BASE}/report", headers=_auth("nope"), json={}).status_code in (401, 403)


class TestSendVideo:
    """POST /video hands a page link to Quick video translate under the token's own rights."""

    def _member(self, email, perms):
        u, token = _member(email)
        for p in perms:
            auth_service.grant_permission(u["id"], p)
        token = svc.create_token(dict(user_id=u["id"], is_admin=False,
                                      permissions=auth_service.effective_permissions(u["id"])), "Phone")["token"]
        return u, token

    def test_member_with_both_rights_starts_a_run(self, isolated_db, monkeypatch):
        from services import quick_video_service
        seen = {}

        def fake_start(principal, url, profile_id, watch_once, holds_paid, at_pc):
            seen.update(user=principal["user_id"], url=url, profile=profile_id, once=watch_once,
                        paid=holds_paid, at_pc=at_pc)
            return {"drama_id": 7, "watch_once": False}

        monkeypatch.setattr(quick_video_service, "start", fake_start)
        u, token = self._member("v@example.com", ["media.import_url", "jobs.start"])
        r = _client().post(f"{BASE}/video", headers=_auth(token), json={"url": "https://youtu.be/abc?t=5"})
        assert r.status_code == 200 and r.json() == {"drama_id": 7}
        assert seen == {"user": u["id"], "url": "https://youtu.be/abc?t=5", "profile": None,
                        "once": False, "paid": False, "at_pc": False}

    @pytest.mark.parametrize("missing", ["media.import_url", "jobs.start"])
    def test_missing_right_is_refused_before_anything_starts(self, isolated_db, monkeypatch, missing):
        from services import quick_video_service
        monkeypatch.setattr(quick_video_service, "start", lambda *a, **k: pytest.fail("started"))
        u, _ = self._member("w@example.com", ["media.import_url", "jobs.start"])
        auth_service.revoke_permission(u["id"], missing)
        token = svc.create_token(dict(user_id=u["id"], is_admin=False,
                                      permissions=auth_service.effective_permissions(u["id"])), "Phone")["token"]
        r = _client().post(f"{BASE}/video", headers=_auth(token), json={"url": "https://youtu.be/abc"})
        assert r.status_code == 403

    def test_a_refusal_from_the_service_is_shown_as_text(self, isolated_db, monkeypatch):
        from lib.errors import InvalidInputError
        from services import quick_video_service

        def refuse(*a, **k):
            raise InvalidInputError("Pick a profile in Settings first.")

        monkeypatch.setattr(quick_video_service, "start", refuse)
        _, token = self._member("x@example.com", ["media.import_url", "jobs.start"])
        r = _client().post(f"{BASE}/video", headers=_auth(token), json={"url": "https://youtu.be/abc"})
        assert r.status_code == 422 and r.json() == {"error": "Pick a profile in Settings first."}

    def test_the_title_limit_keeps_its_409(self, isolated_db, monkeypatch):
        from lib.errors import ConflictError
        from services import quick_video_service

        def full(*a, **k):
            raise ConflictError("You have the most titles allowed.")

        monkeypatch.setattr(quick_video_service, "start", full)
        _, token = self._member("z@example.com", ["media.import_url", "jobs.start"])
        r = _client().post(f"{BASE}/video", headers=_auth(token), json={"url": "https://youtu.be/abc"})
        assert r.status_code == 409

    def test_no_url_is_refused(self, isolated_db):
        _, token = self._member("y@example.com", ["media.import_url", "jobs.start"])
        r = _client().post(f"{BASE}/video", headers=_auth(token), json={})
        assert r.status_code == 400


class TestStartLive:
    """POST /live starts Live on a stream link with the PC's defaults, under the token's rights."""

    def _live_member(self, email, perms=("media.import_url",)):
        u, _ = _member(email)
        for p in perms:
            auth_service.grant_permission(u["id"], p)
        token = svc.create_token(dict(user_id=u["id"], is_admin=False,
                                      permissions=auth_service.effective_permissions(u["id"])), "Phone")["token"]
        return u, token

    def test_starts_with_safe_defaults(self, isolated_db, monkeypatch):
        from services import live_service, model_readiness_service
        seen = {}

        def fake_start(url, **kw):
            seen.update(url=url, **kw)
            return {"session_id": "live_" + "a" * 32}

        # form_options reads the real signature for its defaults.
        monkeypatch.setattr(live_service, "start_session",
                            functools.wraps(live_service.start_session)(fake_start))
        ready = []
        monkeypatch.setattr(model_readiness_service, "require_ready", lambda at_pc, *a, **k: ready.append(at_pc))
        monkeypatch.setattr(live_service, "gpu_available", lambda: False)
        u, token = self._live_member("l@example.com")
        r = _client().post(f"{BASE}/live", headers=_auth(token),
                           json={"url": "https://www.twitch.tv/somebody", "source_language": "ja"})
        assert r.status_code == 200 and r.json() == {"session_id": "live_" + "a" * 32, "joined": False}
        assert seen["url"] == "https://www.twitch.tv/somebody" and seen["source_language"] == "ja"
        assert seen["use_saved_cookies"] is False and seen["principal"]["user_id"] == u["id"]
        assert not seen.get("delayed_player")
        assert ready == [False]
        assert seen["engine"] == "ollama" and seen["use_gpu"] is False

    def test_import_right_is_required(self, isolated_db, monkeypatch):
        from services import live_service
        monkeypatch.setattr(live_service, "start_session", lambda *a, **k: pytest.fail("started"))
        _, token = self._live_member("m@example.com", perms=())
        r = _client().post(f"{BASE}/live", headers=_auth(token), json={"url": "https://www.twitch.tv/x"})
        assert r.status_code == 403

    def test_a_service_refusal_is_plain_text(self, isolated_db, monkeypatch):
        from lib.errors import InvalidInputError
        from services import live_service, model_readiness_service

        def refuse(*a, **k):
            raise InvalidInputError("url must be a public http(s) web address.")

        monkeypatch.setattr(live_service, "start_session",
                            functools.wraps(live_service.start_session)(refuse))
        monkeypatch.setattr(model_readiness_service, "require_ready", lambda *a, **k: None)
        monkeypatch.setattr(live_service, "gpu_available", lambda: False)
        _, token = self._live_member("n@example.com")
        r = _client().post(f"{BASE}/live", headers=_auth(token), json={"url": "http://10.0.0.1/x"})
        assert r.status_code == 422 and "public" in r.json()["error"]

    def test_no_url_is_refused(self, isolated_db):
        _, token = self._live_member("o@example.com")
        assert _client().post(f"{BASE}/live", headers=_auth(token), json={}).status_code == 400

    def test_a_bad_language_value_is_a_422(self, isolated_db):
        _, token = self._live_member("p@example.com")
        r = _client().post(f"{BASE}/live", headers=_auth(token),
                           json={"url": "https://www.twitch.tv/x", "source_language": ["zh"]})
        assert r.status_code == 422


class TestFollow:
    def test_follow_needs_a_token_and_sources_import(self, isolated_db, monkeypatch):
        from services import sources_tracking_service
        monkeypatch.setattr(sources_tracking_service, "follow_from_page",
                            lambda url, principal=None: {"title": "T", "already": False})
        assert _client().post(f"{BASE}/follow", json={"url": "https://a.example/x"}).status_code == 401
        u, token = _member("a@example.com")
        auth_service.revoke_permission(u["id"], "sources.import")
        token = svc.create_token(dict(user_id=u["id"], is_admin=False,
                                      permissions=auth_service.effective_permissions(u["id"])), "Phone")["token"]
        assert _client().post(f"{BASE}/follow", headers=_auth(token), json={"url": "https://a.example/x"}).status_code == 403

    def test_follow_answers_and_maps_errors(self, isolated_db, monkeypatch):
        from lib.errors import InvalidInputError
        from services import sources_tracking_service
        u, _ = _member("a@example.com")
        auth_service.grant_permission(u["id"], "sources.import")
        token = svc.create_token(dict(user_id=u["id"], is_admin=False,
                                      permissions=auth_service.effective_permissions(u["id"])), "Phone")["token"]
        monkeypatch.setattr(sources_tracking_service, "follow_from_page",
                            lambda url, principal=None: {"title": "T", "already": False})
        r = _client().post(f"{BASE}/follow", headers=_auth(token), json={"url": "https://a.example/x"})
        assert r.status_code == 200 and r.json() == {"title": "T", "already": False}
        assert _client().post(f"{BASE}/follow", headers=_auth(token), json={}).status_code == 400

        def refuse(url, principal=None):
            raise InvalidInputError("No source for this site")
        monkeypatch.setattr(sources_tracking_service, "follow_from_page", refuse)
        r = _client().post(f"{BASE}/follow", headers=_auth(token), json={"url": "https://a.example/x"})
        assert r.status_code == 422 and "No source" in r.text


class TestLastPage:
    def test_the_last_page_is_kept_per_account_over_the_bridge(self, isolated_db):
        _, mine = _member("a@example.com")
        _, other = _member("b@example.com")
        r = _client().post(f"{BASE}/last-page", headers=_auth(mine), json={"url": "https://a.example/owl/3", "title": "Owl"})
        assert r.status_code == 200 and r.json() == {"host": "a.example"}
        pages = _client().post(f"{BASE}/last-pages", headers=_auth(mine), json={}).json()["pages"]
        assert pages == [{"host": "a.example", "url": "https://a.example/owl/3", "title": "Owl"}]
        assert _client().post(f"{BASE}/last-pages", headers=_auth(other), json={}).json()["pages"] == []

    def test_it_needs_a_token_and_a_web_address(self, isolated_db):
        assert _client().post(f"{BASE}/last-pages", json={}).status_code == 401
        _, token = _member("a@example.com")
        assert _client().post(f"{BASE}/last-page", headers=_auth(token), json={"url": "file:///x"}).status_code == 422


class TestSites:
    def test_lists_switched_on_sources_with_a_front_page(self, isolated_db):
        from sources import registry
        _, token = _member("a@example.com")
        r = _client().get(f"{BASE}/sites", headers=_auth(token))
        assert r.status_code == 200
        sites = r.json()["sites"]
        assert sites and all(s["url"].startswith("https://") for s in sites)
        assert {s["kind"] for s in sites} <= {"comic", "novel", "video"}
        listed = {s["name"] for s in sites}
        registry.set_enabled("rawkuma", False)
        after = {s["name"] for s in _client().get(f"{BASE}/sites", headers=_auth(token)).json()["sites"]}
        assert listed - after == {"Rawkuma"}

    def test_needs_a_device_token(self, isolated_db):
        assert _client().get(f"{BASE}/sites").status_code == 401


class TestStreamedAnswersHeartbeat:
    """A caller that asks hears "waiting" lines while its pages queue, so a long wait is not a timeout."""

    @staticmethod
    async def _lines(work, heartbeat):
        from api.routers import extension_bridge_routes as routes
        response = await routes._streamed(work, heartbeat=heartbeat)
        return [chunk async for chunk in response.body_iterator]

    def test_a_slow_first_page_is_preceded_by_waiting_lines(self, monkeypatch):
        import asyncio
        import time
        from api.routers import extension_bridge_routes as routes
        monkeypatch.setattr(routes, "_HEARTBEAT_SECONDS", 0.05)

        def work(write):
            time.sleep(0.3)
            write({"page": {"key": "p0"}})
            write({"done": {}})

        lines = asyncio.run(self._lines(work, True))
        assert lines[0] == b'{"waiting": true}\n'
        assert lines[-2:] == [b'{"page": {"key": "p0"}}\n', b'{"done": {}}\n']

    def test_without_the_request_there_are_no_waiting_lines(self, monkeypatch):
        import asyncio
        import time
        from api.routers import extension_bridge_routes as routes
        monkeypatch.setattr(routes, "_HEARTBEAT_SECONDS", 0.05)

        def work(write):
            time.sleep(0.2)
            write({"done": {}})

        assert asyncio.run(self._lines(work, False)) == [b'{"done": {}}\n']

    def test_a_refusal_after_the_answer_started_comes_as_an_error_line(self, monkeypatch):
        import asyncio
        import time
        from api.routers import extension_bridge_routes as routes
        monkeypatch.setattr(routes, "_HEARTBEAT_SECONDS", 0.05)

        def work(write):
            time.sleep(0.2)
            raise page_server.EndpointError(429, "limit spent")

        lines = asyncio.run(self._lines(work, True))
        assert lines[-1] == b'{"error": "limit spent", "status": 429}\n'

    def test_a_stream_with_nothing_real_to_send_ends_at_the_limit(self, monkeypatch):
        import asyncio
        import time
        from api.routers import extension_bridge_routes as routes
        monkeypatch.setattr(routes, "_HEARTBEAT_SECONDS", 0.02)
        monkeypatch.setattr(routes, "_HEARTBEAT_LIMIT_SECONDS", 0.1)

        def work(write):
            time.sleep(0.5)
            write({"done": {}})

        lines = asyncio.run(self._lines(work, True))
        assert lines and set(lines) == {b'{"waiting": true}\n'}


class TestQuickReadRunRoutes:
    """The phone lists and drops its Quick read runs through the bridge."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        from services import quick_read_runs
        monkeypatch.setattr(quick_read_runs, "_runs", {})
        monkeypatch.setattr(extension_bridge_service, "_cancelled_runs", {})
        self.runs = quick_read_runs

    def test_list_then_drop_one_of_my_runs_but_not_someone_elses(self, isolated_db):
        me, token = _member("me@example.com")
        other, _ = _member("other@example.com")
        cancel = extension_bridge_service.mark_cancelled
        self.runs.begin("run-aaaaaaaa", me["id"], "https://site.example/a/b", 4, cancel)
        self.runs.begin("run-bbbbbbbb", other["id"], "https://site.example/a/c", 2, cancel)
        client = _client()
        listed = client.post(f"{BASE}/quick-reads", headers=_auth(token), json={}).json()
        assert [r["run_id"] for r in listed["runs"]] == ["run-aaaaaaaa"]
        assert client.post(f"{BASE}/quick-reads/drop", headers=_auth(token),
                           json={"run_id": "run-bbbbbbbb"}).status_code == 404
        dropped = client.post(f"{BASE}/quick-reads/drop", headers=_auth(token), json={"run_id": "run-aaaaaaaa"})
        assert dropped.json() == {"dropped_runs": 1, "dropped_pages": 4}
        assert extension_bridge_service._run_cancelled("run-aaaaaaaa")
        assert not extension_bridge_service._run_cancelled("run-bbbbbbbb")


class TestPageWithoutRedrawKeepsItsPicture:
    def _png_bytes(self):
        import io
        Image = pytest.importorskip("PIL.Image")
        buffer = io.BytesIO()
        Image.new("RGB", (40, 60), (200, 200, 200)).save(buffer, format="PNG")
        return buffer.getvalue()

    def _job(self, **kw):
        return {"store": False, "typeset": True, "typeset_max_width": 30, **kw}

    def test_a_page_with_nothing_to_redraw_comes_back_as_the_original(self):
        result = {"regions": []}
        extension_bridge_service._show_original_when_not_redrawn(
            result, {"content": self._png_bytes()}, self._job())
        assert result["typeset_image"].startswith("data:image/jpeg;base64,")

    def test_a_redrawn_page_and_a_saved_page_are_left_alone(self):
        redrawn = {"typeset_image": "data:image/jpeg;base64,AAAA"}
        extension_bridge_service._show_original_when_not_redrawn(redrawn, {"content": self._png_bytes()}, self._job())
        assert redrawn["typeset_image"] == "data:image/jpeg;base64,AAAA"
        saved = {}
        extension_bridge_service._show_original_when_not_redrawn(saved, {"content": self._png_bytes()}, self._job(store=True))
        assert "typeset_image" not in saved

    def test_a_picture_pillow_cannot_open_is_ignored(self):
        result = {}
        extension_bridge_service._show_original_when_not_redrawn(result, {"content": b"not a picture"}, self._job())
        assert "typeset_image" not in result


class TestCacheAnsweredRepeatsInARun:
    def test_one_address_listed_twice_is_shown_once_even_with_no_bytes(self):
        pages = [{"key": "a", "content": None, "fetch_url": "https://img.example/1.jpg"},
                 {"key": "b", "content": None, "fetch_url": "https://img.example/1.jpg"},
                 {"key": "c", "content": None, "fetch_url": "https://img.example/2.jpg"}]
        kept, repeats = extension_bridge_service._drop_repeats_in_run(pages, "run-urlrepeat")
        assert [p["key"] for p in kept] == ["a", "c"]
        assert [p["key"] for p in repeats] == ["b"]

    def test_a_page_with_no_bytes_and_no_address_is_kept(self):
        kept, repeats = extension_bridge_service._drop_repeats_in_run(
            [{"key": "a", "content": None}, {"key": "b", "content": None}], "run-nourl")
        assert len(kept) == 2 and not repeats
