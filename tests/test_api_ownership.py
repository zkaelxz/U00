"""
Auth slice B2: ownership enforcement at the API.

B1 (services/ownership_service.py) holds the visibility rule; this checks
that every route applies it. With auth on, user B must get a 404 (never a
403 or a 2xx) for anything under user A's private drama or series, while a
shared item, an admin and the local owner still work, and auth off is
unchanged.

`TestEveryOwnedRouteIsGuarded` is the static half: every route whose path
names a drama or series must go through `require_permission` (which runs
`api.auth.require_path_visible`) or `local_only()` (the owner at the PC),
and a path parameter that looks drama-scoped but isn't `drama_id`/
`series_id` must be listed in `OWNERSHIP_EXEMPT_PARAMS` with a reason.
"""

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from core import Line
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service

REMOTE = "https://baihe.example.com"
NON_ADMIN = auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS + auth_service.OPT_IN_PERMISSIONS

# Path parameters that aren't `drama_id`/`series_id` but name something.
# Each is either nested under a `{drama_id}`/`{series_id}` path (the guard
# covers the parent; the service scopes the child to it) or not drama-scoped.
OWNERSHIP_EXEMPT_PARAMS = {
    "job_id": "jobs: filtered by owner in jobs_routes/job visibility (B2 slice 4)",
    "session_id": "live sessions: filtered by owner (B2 slice 4)",
    "title_id": "discover known_titles: household-wide (plan B, decision 6)",
    "name": "a source adapter name, not an item",
    "notification_id": "source notifications: household-wide (decision 6)",
    "domain": "source profile domain (admin.settings)",
    "kind": "an artifact/profile kind, not an item",
    "engine": "an engine name (PC-only key routes)",
    "package": "a Python package name (PC-only)",
    "preset_id": "presets: household-wide (decision 6)",
    "entry_id": "voice bank: household-wide (decision 6)",
    "bundle_id": "bug bundle (PC-only)",
    "report_id": "bug report (admin.diagnostics / PC-only)",
    "channel": "notification channel (PC-only)",
}
# Children that only appear under a guarded `{drama_id}`/`{series_id}`.
NESTED_PARAMS = {"line_id", "term_id", "note_id", "history_id", "version_id", "page_id",
                 "character_id", "candidate_id", "bulk_job_id", "track", "kind"}


def _app(auth="on"):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False))


def _client(app):
    return TestClient(app, base_url=REMOTE, raise_server_exceptions=False)


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _login(email, admin=False):
    if admin:
        u = auth_service.grant_admin_local(email)
    else:
        u = auth_service.add_user(email)
        for p in auth_service.OPT_IN_PERMISSIONS:
            auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                     api_auth.CSRF_HEADER: s["csrf_token"]}


@pytest.fixture
def world(isolated_db):
    a_id, a = _login("a@example.com")
    b_id, b = _login("b@example.com")
    _adm_id, adm = _login("admin@example.com", admin=True)
    private = db.create_drama(title_en="A private", source_language="zh",
                              owner_user_id=a_id, is_private=1)
    shared = db.create_drama(title_en="A shared", source_language="zh",
                             owner_user_id=a_id, is_private=0)
    pseries = db.create_series("A private series", owner_user_id=a_id, is_private=True)
    in_pseries = db.create_drama(title_en="In A's series", source_language="zh",
                                 series_id=pseries, owner_user_id=a_id)
    return {"a": a, "b": b, "admin": adm, "a_id": a_id, "b_id": b_id,
            "private": private, "shared": shared, "pseries": pseries,
            "in_pseries": in_pseries}


def _fill(path, drama_id, series_id):
    def sub(m):
        name = m.group(1).split(":")[0]
        if name == "drama_id":
            return str(drama_id)
        if name == "series_id":
            return str(series_id)
        return "1" if name.endswith("_id") else "x"
    return re.sub(r"\{([^}]+)\}", sub, path)


def _owned_routes(app):
    for _route, path, methods, decls in api_auth.iter_route_declarations(app):
        if not path.startswith("/api/"):
            continue
        params = set(re.findall(r"\{([^}:]+)", path))
        if params & set(api_auth.OWNED_PATH_PARAMS):
            yield path, methods, decls


class TestEveryOwnedRouteIsGuarded:
    def test_owned_routes_use_a_guarded_declaration(self):
        app = _app()
        bad = [(path, decls) for path, _m, decls in _owned_routes(app)
               if not (len(decls) == 1 and decls[0][0] in ("permission", "local_only"))]
        assert not bad, bad

    def test_every_other_path_param_is_accounted_for(self):
        app = _app()
        unknown = []
        for _route, path, _m, _d in api_auth.iter_route_declarations(app):
            params = set(re.findall(r"\{([^}:]+)", path))
            for p in params - set(api_auth.OWNED_PATH_PARAMS):
                nested = p in NESTED_PARAMS and params & set(api_auth.OWNED_PATH_PARAMS)
                if not nested and p not in OWNERSHIP_EXEMPT_PARAMS:
                    unknown.append((path, p))
        assert not unknown, ("A route names an item by a path parameter the ownership guard "
                             "doesn't know. Use {drama_id}/{series_id}, or add it to "
                             "OWNERSHIP_EXEMPT_PARAMS with a reason: %r" % unknown)


class TestRouteWalk:
    """B calls every drama/series route against A's private items."""

    @pytest.mark.parametrize("target", ["private", "in_pseries"])
    def test_other_users_private_items_are_404_everywhere(self, world, target):
        app = _app()
        client = _client(app)
        seen, wrong = 0, []
        for path, methods, decls in _owned_routes(app):
            kind, perm = decls[0]
            url = _fill(path, world[target], world["pseries"])
            for method in sorted(methods - {"HEAD"}):
                seen += 1
                r = client.request(method, url, headers=world["b"],
                                   json={} if method != "GET" else None)
                # admin.* and PC-only routes refuse B before ownership matters.
                expected = 403 if (kind == "local_only" or perm.startswith("admin.")) else 404
                if r.status_code != expected:
                    wrong.append((method, url, r.status_code))
        assert seen > 100
        assert not wrong, wrong

    def test_owner_and_admin_pass_the_guard(self, world):
        app = _app()
        client = _client(app)
        for who in ("a", "admin"):
            r = client.get(f"/api/library/dramas/{world['private']}", headers=world[who])
            assert r.status_code == 200, (who, r.text)
            r = client.get(f"/api/library/dramas/{world['in_pseries']}", headers=world[who])
            assert r.status_code == 200, (who, r.text)

    def test_shared_drama_visible_to_other_user(self, world):
        client = _client(_app())
        r = client.get(f"/api/library/dramas/{world['shared']}", headers=world["b"])
        assert r.status_code == 200, r.text

    def test_missing_and_invisible_look_the_same(self, world):
        client = _client(_app())
        hidden = client.get(f"/api/library/dramas/{world['private']}", headers=world["b"])
        missing = client.get("/api/library/dramas/999999", headers=world["b"])
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()

    def test_auth_off_unchanged(self, world):
        client = _local(_app("off"))
        r = client.get(f"/api/library/dramas/{world['private']}")
        assert r.status_code == 200, r.text


class TestLibraryLists:
    """B sees shared items and their own, never A's private ones; admin
    and auth off see everything."""

    @pytest.fixture
    def seeded(self, world):
        for did, text in ((world["private"], "秘密"), (world["shared"], "公开"),
                          (world["in_pseries"], "秘密")):
            db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh=text, en="x")])
            db.log_usage(did, "claude", "m", "translate", 10, 5, 0.01)
        return world

    def _ids(self, client, url, headers, key="id"):
        r = client.get(url, headers=headers)
        assert r.status_code == 200, r.text
        return {item[key] for item in r.json()["items"]}

    def test_drama_list(self, seeded):
        w, client = seeded, _client(_app())
        hidden = {w["private"], w["in_pseries"]}
        b_ids = self._ids(client, "/api/library/dramas", w["b"])
        assert w["shared"] in b_ids and not b_ids & hidden
        for who in ("a", "admin"):
            assert hidden <= self._ids(client, "/api/library/dramas", w[who])
        off = _local(_app("off")).get("/api/library/dramas").json()["items"]
        assert hidden <= {d["id"] for d in off}

    def test_recent_costs_series_search(self, seeded):
        w, client = seeded, _client(_app())
        hidden = {w["private"], w["in_pseries"]}
        assert not self._ids(client, "/api/library/recent", w["b"]) & hidden
        assert hidden <= self._ids(client, "/api/library/recent", w["admin"])
        assert not self._ids(client, "/api/library/costs", w["b"]) & hidden
        assert hidden <= self._ids(client, "/api/library/costs", w["admin"])
        # /series lists series holding two or more dramas.
        db.create_drama(title_en="2nd", source_language="zh", series_id=w["pseries"],
                        owner_user_id=w["a_id"])
        assert w["pseries"] not in self._ids(client, "/api/library/series", w["b"])
        assert w["pseries"] in self._ids(client, "/api/library/series", w["a"])
        found = self._ids(client, "/api/library/search?q=秘密", w["b"], key="drama_id")
        assert not found
        assert self._ids(client, "/api/library/search?q=秘密", w["a"], key="drama_id") == hidden
        assert self._ids(client, "/api/library/search?q=公开", w["b"], key="drama_id") \
            == {w["shared"]}

    def test_stats_count_only_visible(self, seeded):
        w, client = seeded, _client(_app())
        b = client.get("/api/library/stats", headers=w["b"]).json()
        adm = client.get("/api/library/stats", headers=w["admin"]).json()
        assert (b["total_dramas"], b["total_lines"]) == (1, 1)
        assert (adm["total_dramas"], adm["total_lines"]) == (3, 3)


class TestDramaIdsInBodies:
    def test_bulk_translate_treats_invisible_as_missing(self, world):
        db.update_drama(world["private"], status="aligned", translation_engine="ollama")
        client = _client(_app())
        r = client.post("/api/library/admin/bulk/translate", headers=world["b"],
                        json={"drama_ids": [world["private"]]})
        assert r.status_code == 422, r.text      # nothing left to queue
        from services import library_admin_service as las
        b = {"user_id": world["b_id"], "is_admin": False, "is_local_owner": False}
        plan = las._bulk_translate_plan([world["private"], 999999], b)
        assert plan[1] == [{"drama_id": world["private"], "reason": "not_found"},
                           {"drama_id": 999999, "reason": "not_found"}]

    def test_source_imports_404_on_invisible_drama(self, world):
        from services import sources_import_service as svc
        from services.service_errors import NotFoundError
        b = {"user_id": world["b_id"], "is_admin": False, "is_local_owner": False}
        with pytest.raises(NotFoundError):
            svc._require_drama(world["private"], b)
        assert svc._require_drama(world["shared"], b)["id"] == world["shared"]
        assert svc._require_drama(world["private"], None)["id"] == world["private"]

    def test_tracking_into_invisible_drama_404(self, world, monkeypatch):
        from services import sources_registry_service as reg
        from services.service_errors import ConflictError, NotFoundError
        monkeypatch.setattr(reg, "_require_source", lambda name: None)
        b = {"user_id": world["b_id"], "is_admin": False, "is_local_owner": False}
        with pytest.raises(NotFoundError, match="No drama"):
            reg.set_tracked("x", "s1", True, drama_id=world["private"], principal=b)
        with pytest.raises(ConflictError):     # past the drama check: series not loaded
            reg.set_tracked("x", "s1", True, drama_id=world["shared"], principal=b)


class TestTranslateHistory:
    def test_users_see_only_their_own_rows(self, world):
        db.save_translate_history("zh", "en", "ollama", "a-text", "A", user_id=world["a_id"])
        db.save_translate_history("zh", "en", "ollama", "b-text", "B", user_id=world["b_id"])
        db.save_translate_history("zh", "en", "ollama", "pc-text", "PC")
        client = _client(_app())

        def texts(who):
            r = client.get("/api/translate/history", headers=world[who])
            assert r.status_code == 200, r.text
            return {i["source_text"] for i in r.json()["items"]}
        assert texts("b") == {"b-text"}
        assert texts("a") == {"a-text"}
        assert texts("admin") == {"a-text", "b-text", "pc-text"}
        off = _local(_app("off")).get("/api/translate/history").json()["items"]
        assert len(off) == 3
