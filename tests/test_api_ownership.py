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
a path parameter that looks drama-scoped but isn't `drama_id`/
`series_id` must be listed in `OWNERSHIP_EXEMPT_PARAMS` with a reason,
every job/Live-session route must be listed in `JOB_ROUTES`, and every
call from api/ to a service taking `principal=None` must pass one.
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
    "revision": "a Hugging Face cache revision hash (model-cache delete, PC-only)",
    "voice": "a downloaded Piper voice name (model-cache delete, PC-only)",
}
# Routes naming a job or Live session. The path guard can't see these, so
# each one is listed with the owner check its service runs (review L-4): a
# new `/.../{job_id}/...` route fails the static test until it's added here,
# and TestJobs.test_every_job_route_hides_other_users_jobs walks them all.
JOB_ROUTES = {
    ("GET", "/api/jobs/{job_id}"): "jobs_service.get_job -> can_see_job",
    ("POST", "/api/jobs/{job_id}/cancel"): "jobs_service.cancel_job -> can_see_job",
    ("GET", "/api/sources/jobs/{job_id}/result"):
        "sources_search_service.get_job_result -> can_see_job",
    ("GET", "/api/live/sessions/{session_id}"): "live_service.get_session -> can_see_job",
    ("POST", "/api/live/sessions/{session_id}/stop"):
        "live_service.stop_session -> can_see_job",
}
JOB_PARAMS = {"job_id", "session_id"}
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
    adm_id, adm = _login("admin@example.com", admin=True)
    private = db.create_drama(title_en="A private", source_language="zh",
                              owner_user_id=a_id, is_private=1)
    shared = db.create_drama(title_en="A shared", source_language="zh",
                             owner_user_id=a_id, is_private=0)
    pseries = db.create_series("A private series", owner_user_id=a_id, is_private=True)
    in_pseries = db.create_drama(title_en="In A's series", source_language="zh",
                                 series_id=pseries, owner_user_id=a_id)
    return {"a": a, "b": b, "admin": adm, "a_id": a_id, "b_id": b_id, "admin_id": adm_id,
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
            if params & JOB_PARAMS:
                continue                    # test_job_routes_are_listed
            for p in params - set(api_auth.OWNED_PATH_PARAMS):
                nested = p in NESTED_PARAMS and params & set(api_auth.OWNED_PATH_PARAMS)
                if not nested and p not in OWNERSHIP_EXEMPT_PARAMS:
                    unknown.append((path, p))
        assert not unknown, ("A route names an item by a path parameter the ownership guard "
                             "doesn't know. Use {drama_id}/{series_id}, or add it to "
                             "OWNERSHIP_EXEMPT_PARAMS with a reason: %r" % unknown)

    def test_job_routes_are_listed(self):
        app = _app()
        found = set()
        for _route, path, methods, _d in api_auth.iter_route_declarations(app):
            if set(re.findall(r"\{([^}:]+)", path)) & JOB_PARAMS:
                found |= {(m, path) for m in methods - {"HEAD"}}
        assert found == set(JOB_ROUTES), (
            "A route names a job or Live session. Check the caller can see it "
            "(ownership_service.can_see_job) and list it in JOB_ROUTES: %r"
            % (found ^ set(JOB_ROUTES)))

    def test_api_always_passes_the_principal(self):
        # Services take `principal=None` to mean auth off (Streamlit, the CLI);
        # from the API that would grant full visibility. Every call from api/
        # to a service function with that default must pass one explicitly.
        import ast
        import importlib
        import inspect
        import pathlib
        checked, bad = 0, []
        for path in sorted(pathlib.Path(api_auth.__file__).parent.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                        and node.module.split(".")[0] == "services":
                    for a in node.names:
                        names[a.asname or a.name] = (
                            importlib.import_module(f"services.{a.name}")
                            if node.module == "services"
                            else getattr(importlib.import_module(node.module), a.name, None))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f, fn = node.func, None
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                        and inspect.ismodule(names.get(f.value.id)):
                    fn = getattr(names[f.value.id], f.attr, None)
                elif isinstance(f, ast.Name) and inspect.isfunction(names.get(f.id)):
                    fn = names[f.id]
                if not inspect.isfunction(fn):
                    continue
                param = inspect.signature(fn).parameters.get("principal")
                if param is None or param.default is inspect.Parameter.empty:
                    continue            # a required one: Python refuses a missing one
                checked += 1
                passed = {k.arg: k.value for k in node.keywords}.get("principal")
                if passed is None or (isinstance(passed, ast.Constant) and passed.value is None):
                    bad.append(f"{path.name}:{node.lineno} {fn.__qualname__}")
        assert checked > 20
        assert not bad, bad


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

    def test_continue_and_filter_options(self, seeded):
        w, client = seeded, _client(_app())
        hidden = {w["private"], w["in_pseries"]}
        db.update_drama(w["private"], studio="Secret Studio", custom_tags="secret-tag")
        db.update_drama(w["shared"], studio="Open Studio")
        for did in (w["private"], w["shared"], w["in_pseries"]):
            db.save_progress(did, percent_complete=40.0)
        b_ids = self._ids(client, "/api/library/continue", w["b"], key="drama_id")
        assert w["shared"] in b_ids and not b_ids & hidden
        assert hidden <= self._ids(client, "/api/library/continue", w["admin"], key="drama_id")
        b_opts = client.get("/api/library/filter-options", headers=w["b"]).json()
        assert b_opts["studios"] == ["Open Studio"] and "secret-tag" not in b_opts["custom_tags"]
        a_opts = client.get("/api/library/filter-options", headers=w["a"]).json()
        assert "Secret Studio" in a_opts["studios"] and "secret-tag" in a_opts["custom_tags"]

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


    def test_tracked_series_hide_an_invisible_linked_drama(self, world):
        # Tracked series are household-wide; the linked private drama's id isn't.
        from sources import store
        store.track_series("manhuagui", "1", "Priv", drama_id=world["private"])
        store.track_series("manhuagui", "2", "Shared", drama_id=world["shared"])
        store.track_series("manhuagui", "3", "Unlinked")
        client = _client(_app())

        def linked(who):
            r = client.get("/api/sources/tracked", headers=world[who])
            assert r.status_code == 200, r.text
            return {t["series_id"]: t["drama_id"] for t in r.json()}
        assert linked("b") == {"1": None, "2": world["shared"], "3": None}
        expected = {"1": world["private"], "2": world["shared"], "3": None}
        assert linked("a") == expected
        assert linked("admin") == expected

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


class TestNewItemsAreStamped:
    def _row(self, did):
        return db.get_item_ownership("drama", did)

    def test_api_create_stamps_creator_and_share_default(self, world):
        client = _client(_app())
        r = client.post("/api/dramas", headers=world["admin"],
                        json={"source_language": "zh", "title_en": "Mine"})
        assert r.status_code == 201, r.text
        row = self._row(r.json()["id"])
        assert row["owner_user_id"] == world["admin_id"]
        assert row["is_private"] == 0                   # shares by default

    def test_service_create_uses_share_by_default(self, world):
        from services import drama_service, ownership_service
        a = {"user_id": world["a_id"], "is_admin": False, "is_local_owner": False}
        ownership_service.set_share_by_default(a, False)
        d = drama_service.create_drama(source_language="zh", title_en="Hidden", principal=a)
        row = self._row(d["id"])
        assert (row["owner_user_id"], row["is_private"]) == (world["a_id"], 1)
        b = {"user_id": world["b_id"], "is_admin": False, "is_local_owner": False}
        assert not ownership_service.can_see_drama(b, d["id"])

    def test_new_series_and_drama_in_it_owned_by_creator(self, world):
        from services import drama_service
        a = {"user_id": world["a_id"], "is_admin": False, "is_local_owner": False}
        d = drama_service.create_drama(source_language="zh", title_en="Ep1",
                                       new_series_name="A's new series", principal=a)
        row = self._row(d["id"])
        assert row["owner_user_id"] == world["a_id"]
        s = db.get_item_ownership("series", row["series_id"])
        assert s["owner_user_id"] == world["a_id"]

    def test_own_drama_into_own_private_series(self, world):
        from services import drama_service
        a = {"user_id": world["a_id"], "is_admin": False, "is_local_owner": False}
        d = drama_service.create_drama(source_language="zh", title_en="Ep2",
                                       series_id=world["pseries"], principal=a)
        assert self._row(d["id"])["series_id"] == world["pseries"]

    def test_auth_off_create_is_pc_owned(self, isolated_db):
        from services import drama_service
        d = drama_service.create_drama(source_language="zh", title_en="PC")
        row = self._row(d["id"])
        assert (row["owner_user_id"], row["is_private"]) == (None, 0)

    def test_discover_import_stamps_owner(self, world):
        from services import discover_catalog_service as svc
        a = {"user_id": world["a_id"], "is_admin": False, "is_local_owner": False}
        tid = db.create_known_title(title_en="Known", language="zh", media_type="audio_drama")
        d = svc.import_to_library(tid, principal=a)
        assert self._row(d["id"])["owner_user_id"] == world["a_id"]


class TestJobs:
    def test_job_records_its_starter(self, world):
        import background_jobs
        from api.auth import require_permission
        app = _app()

        @app.post("/api/zz-start", dependencies=[require_permission("jobs.start")])
        def start():
            background_jobs.start_job("zz_owner_probe", lambda: None)
            return {}
        r = _client(app).post("/api/zz-start", headers=world["b"])
        assert r.status_code == 200, r.text
        assert background_jobs.get_status("zz_owner_probe")["owner_user_id"] == world["b_id"]
        assert db.get_job_record("zz_owner_probe")["owner_user_id"] == world["b_id"]
        # Outside a request (Streamlit, the CLI, a job's own thread): the PC.
        background_jobs.start_job("zz_owner_probe2", lambda: None)
        assert background_jobs.get_status("zz_owner_probe2")["owner_user_id"] is None

    def test_queued_gpu_message_does_not_name_another_users_job(self, world):
        # Review M-1: B's queued job must not reveal A's private drama title.
        import threading
        import background_jobs
        from api.auth import require_permission
        app = _app()
        release, started = threading.Event(), threading.Event()
        a_job, b_job = f"transcribe_{world['private']}", f"transcribe_{world['shared']}"

        @app.post("/api/zz-gpu/{who}", dependencies=[require_permission("jobs.start")])
        def start(who: str):
            if who == "a":
                background_jobs.start_job(a_job, lambda: (started.set(), release.wait(5)),
                                          gpu_touching=True,
                                          description="Ollama translation (A private)")
            else:
                background_jobs.start_job(b_job, lambda: None, gpu_touching=True,
                                          description="Ollama translation (A shared)")
            return {}
        background_jobs.set_gpu_limit_enabled(True)
        client = _client(app)
        try:
            assert client.post("/api/zz-gpu/a", headers=world["a"]).status_code == 200
            assert started.wait(5)
            assert client.post("/api/zz-gpu/b", headers=world["b"]).status_code == 200
            r = client.get(f"/api/jobs/{b_job}", headers=world["b"])
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "queued"
            assert "A private" not in r.text and a_job not in r.text
            assert "A private" not in (db.get_job_record(b_job)["message"] or "")
        finally:
            release.set()
            for jid in (a_job, b_job):
                for _ in range(100):
                    st = background_jobs.get_status(jid)
                    if not st or st["status"] not in ("queued", "running"):
                        break
                    threading.Event().wait(0.05)
                background_jobs.clear_job(jid)

    @pytest.fixture
    def jobs(self, world):
        w = world
        rows = {"priv": f"translate_{w['private']}", "shared": f"translate_{w['shared']}",
                "a_fixed": "discover_bulk_extract", "pc_fixed": "library_backup",
                "b_fixed": "sources_search"}
        owners = {"priv": w["a_id"], "shared": w["a_id"], "a_fixed": w["a_id"],
                  "pc_fixed": None, "b_fixed": w["b_id"]}
        for key, job_id in rows.items():
            db.save_job_record(job_id, "running", started_at=1.0, owner_user_id=owners[key])
        return rows

    def test_list_get_cancel(self, world, jobs):
        client = _client(_app())
        seen = {j["job_id"] for j in client.get("/api/jobs", headers=world["b"]).json()["items"]}
        assert seen == {jobs["shared"], jobs["b_fixed"]}
        assert {j["job_id"] for j in client.get(
            "/api/jobs", headers=world["admin"]).json()["items"]} >= set(jobs.values())
        a_seen = {j["job_id"] for j in client.get("/api/jobs", headers=world["a"]).json()["items"]}
        assert a_seen == {jobs["priv"], jobs["shared"], jobs["a_fixed"]}
        for key in ("priv", "a_fixed", "pc_fixed"):
            assert client.get(f"/api/jobs/{jobs[key]}", headers=world["b"]).status_code == 404
            assert client.post(f"/api/jobs/{jobs[key]}/cancel",
                               headers=world["b"]).status_code == 404
        assert client.get(f"/api/jobs/{jobs['shared']}", headers=world["b"]).status_code == 200
        assert client.get(f"/api/jobs/{jobs['priv']}", headers=world["admin"]).status_code == 200
        off = _local(_app("off")).get("/api/jobs").json()["items"]
        assert {j["job_id"] for j in off} >= set(jobs.values())

    def test_starter_loses_a_drama_job_when_the_drama_goes_private(self, world):
        # Review L-1: B started a run on A's shared drama; A then made it private.
        from services import ownership_service
        job_id = f"translate_{world['shared']}"
        db.save_job_record(job_id, "running", started_at=1.0, owner_user_id=world["b_id"])
        db.save_job_record("sources_search", "running", started_at=1.0,
                           owner_user_id=world["b_id"])
        client = _client(_app())
        assert client.get(f"/api/jobs/{job_id}", headers=world["b"]).status_code == 200
        a = {"user_id": world["a_id"], "is_admin": False, "is_local_owner": False}
        ownership_service.set_private(a, "drama", world["shared"], True)
        assert client.get(f"/api/jobs/{job_id}", headers=world["b"]).status_code == 404
        assert client.post(f"/api/jobs/{job_id}/cancel", headers=world["b"]).status_code == 404
        seen = {j["job_id"] for j in client.get("/api/jobs", headers=world["b"]).json()["items"]}
        assert seen == {"sources_search"}          # a non-drama job stays the starter's
        assert client.get(f"/api/jobs/{job_id}", headers=world["a"]).status_code == 200

    def test_shared_fixed_id_results_are_per_starter(self, world, monkeypatch):
        import background_jobs
        from services import live_service
        status = {"status": "done", "progress": 1.0, "message": "", "result": {"rows": []},
                  "owner_user_id": world["a_id"]}
        monkeypatch.setattr(background_jobs, "get_status",
                            lambda job_id: dict(status) if job_id in (
                                "discover_bulk_extract", "discover_navigation_help",
                                "sources_search", "live_" + "a" * 32) else None)
        sid = "live_" + "a" * 32
        monkeypatch.setitem(live_service._sessions, sid,
                            {"dir": None, "engine": "ollama", "owner_user_id": world["a_id"]})
        client = _client(_app())
        urls = ["/api/discover/bulk-extract/result", "/api/discover/navigation-help/result",
                "/api/sources/jobs/sources_search/result", f"/api/live/sessions/{sid}"]
        for url in urls:
            assert client.get(url, headers=world["b"]).status_code == 404, url
            assert client.get(url, headers=world["a"]).status_code == 200, url
            assert client.get(url, headers=world["admin"]).status_code == 200, url
        assert client.post(f"/api/live/sessions/{sid}/stop",
                           headers=world["b"]).status_code == 404
        listed = client.get("/api/live/sessions", headers=world["b"]).json()
        assert sid not in {s["session_id"] for s in listed}
        listed = client.get("/api/live/sessions", headers=world["a"]).json()
        assert sid in {s["session_id"] for s in listed}

    def test_every_job_route_hides_other_users_jobs(self, world, monkeypatch):
        import background_jobs
        from services import live_service
        sid = "live_" + "b" * 32
        status = {"status": "done", "progress": 1.0, "message": "", "result": {"rows": []},
                  "owner_user_id": world["a_id"]}
        monkeypatch.setattr(background_jobs, "get_status",
                            lambda job_id: dict(status) if job_id in ("sources_search", sid)
                            else None)
        monkeypatch.setitem(live_service._sessions, sid,
                            {"dir": None, "engine": "ollama", "owner_user_id": world["a_id"]})
        db.save_job_record("sources_search", "done", started_at=1.0, owner_user_id=world["a_id"])
        client = _client(_app())
        for method, path in JOB_ROUTES:
            url = path.replace("{job_id}", "sources_search").replace("{session_id}", sid)
            r = client.request(method, url, headers=world["b"])
            assert r.status_code == 404, (method, url, r.status_code)
            if method == "GET":
                assert client.get(url, headers=world["a"]).status_code == 200, url

    def test_import_job_result_follows_drama_visibility(self, world, monkeypatch):
        import background_jobs
        status = {"status": "running", "progress": 0.5, "message": "", "result": None,
                  "owner_user_id": None}
        monkeypatch.setattr(background_jobs, "get_status", lambda job_id: dict(status))
        client = _client(_app())
        priv = f"/api/sources/jobs/sourceimport_{world['private']}/result"
        shared = f"/api/sources/jobs/sourceimport_{world['shared']}/result"
        assert client.get(priv, headers=world["b"]).status_code == 404
        assert client.get(shared, headers=world["b"]).status_code == 200
        assert client.get(priv, headers=world["a"]).status_code == 200


def test_new_run_over_a_stale_running_record_takes_the_new_owner(isolated_db):
    # A crashed process leaves "running" with owner 1; the next run is user 2's.
    db.save_job_record("sources_search", "running", started_at=1.0, owner_user_id=1)
    db.save_job_record("sources_search", "running", started_at=2.0, owner_user_id=2)
    assert db.get_job_record("sources_search")["owner_user_id"] == 2
    # Progress updates of the same run keep it.
    db.save_job_record("sources_search", "done", started_at=2.0, owner_user_id=None)
    assert db.get_job_record("sources_search")["owner_user_id"] == 2
