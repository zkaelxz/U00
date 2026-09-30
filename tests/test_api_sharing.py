"""The Sharing routes (api/routers/sharing_routes.py): who can see each
drama and series, and each person's "share new items" setting."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, ownership_service

REMOTE = "https://baihe.example.com"


def _app(auth="on"):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False))


def _client(app):
    return TestClient(app, base_url=REMOTE, raise_server_exceptions=False)


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _login(email, name="", admin=False, revoke=()):
    u = auth_service.grant_admin_local(email) if admin else auth_service.add_user(email)
    if name:
        db.auth_update_user(u["id"], display_name=name)
    for p in revoke:
        auth_service.revoke_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                     api_auth.CSRF_HEADER: s["csrf_token"]}


@pytest.fixture
def world(isolated_db):
    a_id, a = _login("ann@example.com", "Ann")
    b_id, b = _login("bo@example.com")
    adm_id, adm = _login("admin@example.com", "Boss", admin=True)
    w = {"a": a, "b": b, "admin": adm, "a_id": a_id, "b_id": b_id, "admin_id": adm_id}
    w["solo"] = db.create_drama(title_en="Solo", source_language="zh", owner_user_id=a_id)
    w["hidden"] = db.create_drama(title_en="Hidden", source_language="zh",
                                  owner_user_id=a_id, is_private=1)
    w["series"] = db.create_series("Saga", owner_user_id=a_id)
    w["ep1"] = db.create_drama(title_en="Ep 1", source_language="zh", series_id=w["series"],
                               owner_user_id=a_id)
    w["pc"] = db.create_drama(title_zh="旧")
    return w


def _flip(client, who, kind, item_id, private):
    return client.post(f"/api/sharing/{kind}/{item_id}/private", headers=who,
                       json={"private": private})


def _private(kind, item_id):
    return db.get_item_ownership(kind, item_id)["is_private"]


class TestFlip:
    def test_owner_can_flip_their_own_drama(self, world):
        c = _client(_app())
        r = _flip(c, world["a"], "dramas", world["solo"], True)
        assert r.status_code == 200, r.text
        assert r.json() == {"kind": "drama", "id": world["solo"], "is_private": True}
        assert _private("drama", world["solo"]) == 1
        assert _flip(c, world["a"], "dramas", world["solo"], False).status_code == 200
        assert _private("drama", world["solo"]) == 0

    def test_other_user_gets_403_for_visible_and_404_for_invisible(self, world):
        c = _client(_app())
        r = _flip(c, world["b"], "dramas", world["solo"], True)
        assert r.status_code == 403
        assert _private("drama", world["solo"]) == 0
        hidden = _flip(c, world["b"], "dramas", world["hidden"], False)
        missing = _flip(c, world["b"], "dramas", 999999, False)
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()
        assert _private("drama", world["hidden"]) == 1
        assert _flip(c, world["b"], "series", world["series"], True).status_code == 403
        secret = db.create_series("Secret saga", owner_user_id=world["a_id"], is_private=True)
        hidden = _flip(c, world["b"], "series", secret, False)
        missing = _flip(c, world["b"], "series", 999999, False)
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()
        assert _private("series", secret) == 1

    def test_admin_can_flip_anyones_item(self, world):
        c = _client(_app())
        assert _flip(c, world["admin"], "dramas", world["hidden"], False).status_code == 200
        assert _private("drama", world["hidden"]) == 0
        assert _flip(c, world["admin"], "series", world["series"], True).status_code == 200
        assert _private("series", world["series"]) == 1
        assert _flip(c, world["admin"], "dramas", world["pc"], True).status_code == 200

    def test_series_conflicts_are_409_with_a_plain_message(self, world):
        c = _client(_app())
        for private in (True, False):
            r = _flip(c, world["a"], "dramas", world["ep1"], private)
            assert r.status_code == 409
            assert r.json()["error"]["message"] == "Make the whole series private instead"
        db.create_drama(title_en="Bo's ep", source_language="zh", series_id=world["series"],
                        owner_user_id=world["b_id"])
        r = _flip(c, world["admin"], "series", world["series"], True)
        assert r.status_code == 409
        assert "other people's dramas" in r.json()["error"]["message"]
        assert _private("series", world["series"]) == 0

    def test_series_with_a_pc_drama_is_admin_only(self, world):
        c = _client(_app())
        db.update_drama(world["pc"], series_id=world["series"])
        r = _flip(c, world["a"], "series", world["series"], True)
        assert r.status_code == 409
        assert r.json()["error"]["message"] == "This series holds a drama owned at the PC; ask an admin."
        assert _flip(c, world["admin"], "series", world["series"], True).status_code == 200

    def test_flips_are_audited_with_the_actor(self, world):
        c = _client(_app())
        assert _flip(c, world["b"], "dramas", world["solo"], True).status_code == 403
        assert _flip(c, world["a"], "dramas", world["solo"], True).status_code == 200
        rows = [r for r in db.auth_list_audit(50) if r["action"] == "sharing.set_private"]
        assert [(r["user_id"], r["detail_redacted"]) for r in rows] == \
            [(world["a_id"], f"drama {world['solo']}: private=True")]

    def test_needs_lines_edit_and_a_valid_body(self, world):
        c = _client(_app())
        _cid, ro = _login("ro@example.com", revoke=("lines.edit",))
        assert _flip(c, ro, "dramas", world["pc"], True).status_code == 403
        r = c.post(f"/api/sharing/dramas/{world['solo']}/private", headers=world["a"],
                   json={"private": True, "owner_user_id": 5})
        assert r.status_code == 422
        r = c.post(f"/api/sharing/dramas/{world['solo']}/private", headers=world["a"], json={})
        assert r.status_code == 422
        assert _private("drama", world["solo"]) == 0

    def test_auth_off_local_owner_can_flip(self, world):
        c = _local(_app("off"))
        assert _flip(c, {}, "dramas", world["hidden"], False).status_code == 200
        assert _flip(c, {}, "dramas", world["ep1"], True).status_code == 409


class TestList:
    def test_admin_sees_every_item_grouped_without_emails(self, world):
        db.auth_update_user(world["a_id"], display_name="")      # falls back to "User <id>"
        ownership_service.set_private(None, "series", world["series"], True)
        r = _client(_app()).get("/api/sharing/items", headers=world["admin"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert "@" not in r.text and "example.com" not in r.text
        assert body["total"] == 5 and body["offset"] == 0 and body["limit"] == 100
        order = [(i["kind"], i["title"]) for i in body["items"]]
        assert order == [("drama", "Hidden"), ("series", "Saga"), ("drama", "Ep 1"),
                         ("drama", "Solo"), ("drama", "旧")]
        by = {(i["kind"], i["id"]): i for i in body["items"]}
        ep1 = by[("drama", world["ep1"])]
        assert (ep1["series_id"], ep1["series_name"], ep1["series_is_private"]) == \
            (world["series"], "Saga", True)
        assert by[("series", world["series"])]["is_private"] is True
        assert by[("drama", world["pc"])]["owner_name"] == "PC owner"
        assert by[("drama", world["pc"])]["created_at_pc"] is True
        assert by[("drama", world["solo"])]["created_at_pc"] is False
        assert by[("drama", world["solo"])]["owner_name"] == f"User {world['a_id']}"
        assert by[("drama", world["solo"])]["series_is_private"] is None

    def test_display_names_and_removed_users(self, world):
        db.update_drama(world["pc"], owner_user_id=world["admin_id"])
        orphan = db.create_drama(title_en="Orphan", source_language="zh", owner_user_id=424242)
        db.auth_update_user(world["b_id"], display_name="bo@example.com")
        bos = db.create_drama(title_en="Bo's", source_language="zh", owner_user_id=world["b_id"])
        r = _client(_app()).get("/api/sharing/items", headers=world["admin"])
        assert "@" not in r.text
        names = {i["id"]: i["owner_name"] for i in r.json()["items"] if i["kind"] == "drama"}
        assert names[world["solo"]] == "Ann" and names[world["pc"]] == "Boss"
        assert names[orphan] == "Removed user"
        assert names[bos] == f"User {world['b_id']}"

    def test_paged_and_bounded(self, world):
        c = _client(_app())
        page = c.get("/api/sharing/items?offset=1&limit=2", headers=world["admin"]).json()
        assert page["total"] == 5 and len(page["items"]) == 2
        assert [i["title"] for i in page["items"]] == ["Saga", "Ep 1"]
        for q in ("limit=0", "limit=201", "offset=-1", "limit=x"):
            assert c.get(f"/api/sharing/items?{q}", headers=world["admin"]).status_code == 422

    def test_household_users_are_refused(self, world):
        r = _client(_app()).get("/api/sharing/items", headers=world["a"])
        assert r.status_code == 403
        with pytest.raises(Exception):
            ownership_service.list_sharing({"user_id": world["a_id"], "is_admin": False,
                                            "is_local_owner": False})

    def test_local_owner_and_auth_off(self, world):
        r = _local(_app("off")).get("/api/sharing/items")
        assert r.status_code == 200 and r.json()["total"] == 5
        assert ownership_service.list_sharing(api_auth.local_owner_principal())["total"] == 5


class TestShareByDefault:
    def test_off_by_default_and_per_person(self, world):
        c = _client(_app())
        for who in ("a", "b", "admin"):
            r = c.get("/api/sharing/share-by-default", headers=world[who])
            assert r.status_code == 200 and r.json() == {"share_by_default": False}
        r = c.post("/api/sharing/share-by-default", headers=world["a"],
                   json={"share_by_default": True})
        assert r.status_code == 200 and r.json() == {"share_by_default": True}
        assert c.get("/api/sharing/share-by-default",
                     headers=world["a"]).json()["share_by_default"] is True
        assert c.get("/api/sharing/share-by-default",
                     headers=world["b"]).json()["share_by_default"] is False
        # Only new items: nothing A already has changes.
        assert _private("drama", world["hidden"]) == 1 and _private("drama", world["solo"]) == 0

    def test_validation_and_permissions(self, world):
        c = _client(_app())
        assert c.post("/api/sharing/share-by-default", headers=world["a"],
                      json={"share_by_default": True, "user_id": 1}).status_code == 422
        _cid, ro = _login("ro@example.com", revoke=("lines.edit",))
        assert c.get("/api/sharing/share-by-default", headers=ro).status_code == 200
        assert c.post("/api/sharing/share-by-default", headers=ro,
                      json={"share_by_default": True}).status_code == 403
        assert _client(_app()).get("/api/sharing/share-by-default").status_code == 401

    def test_auth_off_uses_the_household_setting(self, world):
        c = _local(_app("off"))
        assert c.get("/api/sharing/share-by-default").json() == {"share_by_default": False}
        assert c.post("/api/sharing/share-by-default",
                      json={"share_by_default": True}).status_code == 200
        assert db.get_app_setting(ownership_service.HOUSEHOLD_SHARE_KEY) is True
        assert ownership_service.new_item_defaults(None)["is_private"] == 0
