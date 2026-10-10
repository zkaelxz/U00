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
    "pack_id": "a built-in language pack id, not an item (read-only data shipped with the app)",
    "language": "a source language code for the language-pack default (admin.settings)",
    "name": "a source adapter name or a model file name, not an item",
    "notification_id": "source notifications: household-wide (decision 6)",
    "chapter_id": "a source chapter id, not an item; the AI-recover route takes the drama in "
                  "its body and sources_import_service._require_drama checks ownership",
    "domain": "source profile domain (admin.settings)",
    "kind": "an artifact/profile kind, not an item",
    "engine": "an engine name (PC-only key routes, engine Test)",
    "capability": "a Step 36 task capability id, not an item (settings)",
    "package": "a Python package name (PC-only)",
    "preset_id": "presets: household-wide (decision 6)",
    "entry_id": "voice bank: household-wide (decision 6)",
    "report_id": "bug report (admin.diagnostics / PC-only)",
    "channel": "notification channel (PC-only)",
    "revision": "a Hugging Face model-cache revision, not an item (PC-only delete)",
    "backlog_id": "maintenance-assistant backlog item: app-wide, PC-only (Step 42)",
    "case_id": "benchmark case: household-wide admin tool (admin.diagnostics / PC-only)",
    "run_id": "benchmark run record: household-wide admin tool (admin.diagnostics)",
    "model_candidate_id": "re-evaluation candidate model: household-wide (PC-only writes)",
    "user_id": "a user account, not an owned item (admin.users only)",
    "auth_session_id": "the caller's own sign-in session; auth_service scopes it to the "
                       "caller's user id from their session (404 otherwise)",
    "device_token_id": "an extension device token: the own route scopes it to the caller's "
                       "user id from their session (404 otherwise); the route for "
                       "everyone's tokens is local_only()",
}
# Routes naming a job or Live session. The path guard can't see these, so
# each one is listed with the owner check its service runs (review L-4): a
# new `/.../{job_id}/...` route fails the static test until it's added here,
# and TestJobs.test_every_job_route_hides_other_users_jobs walks them all.
JOB_ROUTES = {
    ("GET", "/api/jobs/{job_id}"): "jobs_service.get_job -> can_see_job",
    ("POST", "/api/jobs/{job_id}/cancel"): "jobs_service.cancel_job -> can_see_job",
    ("POST", "/api/jobs/{job_id}/delete"): "jobs_service.delete_job -> can_see_job",
    ("POST", "/api/jobs/{job_id}/force-stop"): "jobs_service.force_stop_job -> can_see_job",
    ("GET", "/api/jobs/{job_id}/stages"): "jobs_service.get_job_stages -> can_see_job",
    ("GET", "/api/sources/jobs/{job_id}/result"):
        "sources_search_service.get_job_result -> can_see_job",
    ("GET", "/api/live/sessions/{session_id}"): "live_service.get_session -> can_see_job",
    ("POST", "/api/live/sessions/{session_id}/stop"):
        "live_service.stop_session -> can_see_job",
}
JOB_PARAMS = {"job_id", "session_id"}
# Children that only appear under a guarded `{drama_id}`/`{series_id}`.
NESTED_PARAMS = {"line_id", "term_id", "note_id", "history_id", "version_id", "page_id",
                 "character_id", "candidate_id", "bulk_job_id", "track", "kind", "number"}


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


def _service_names(tree, module_name):
    """{local name: services module or function} for a module's imports."""
    import ast
    import importlib
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if node.level:
                mod = module_name.rsplit(".", node.level)[0] + "." + mod
            if mod.split(".")[0] != "services":
                continue
            for a in node.names:
                names[a.asname or a.name] = (
                    importlib.import_module(f"services.{a.name}") if mod == "services"
                    else getattr(importlib.import_module(mod), a.name, None))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("services.") and a.asname:
                    names[a.asname] = importlib.import_module(a.name)
    return names


def _principal_calls(func, names, module):
    """(call node, service function, the expression passed as `principal` or
    None) for every call in `func` to a function whose `principal` defaults
    to None (auth off, sees everything) -- called directly, or handed to a
    runner such as run_in_threadpool(fn, ..., principal=...). A positional
    principal counts. Names resolve through the module's service imports,
    then the module's own globals (a same-module helper)."""
    import ast
    import inspect

    def resolve(node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and inspect.ismodule(names.get(node.value.id)):
            return getattr(names[node.value.id], node.attr, None)
        if isinstance(node, ast.Name):
            return names.get(node.id) or getattr(module, node.id, None)
        return None

    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        candidates = [(resolve(node.func), node.args)]
        candidates += [(resolve(a), node.args[i + 1:]) for i, a in enumerate(node.args)]
        for fn, args in candidates:
            if not inspect.isfunction(fn):
                continue
            params = inspect.signature(fn).parameters
            param = params.get("principal")
            if param is None or param.default is inspect.Parameter.empty:
                continue
            passed = {k.arg: k.value for k in node.keywords}.get("principal")
            positional = [p for p in params.values()
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            index = positional.index(param) if param in positional else None
            if passed is None and index is not None and index < len(args) \
                    and not any(isinstance(a, ast.Starred) for a in args[:index + 1]):
                passed = args[index]
            if passed is None and any(k.arg is None for k in node.keywords):
                passed = node          # **kwargs: can't tell, treated as passed
            yield node, fn, passed


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

    def test_route_handlers_pass_the_request_principal(self):
        # The test above checks every call from api/ by name. This one walks
        # the dispatchable routes and also catches a principal-taking service
        # handed to a runner (run_in_threadpool(fn, ..., principal=...)), and
        # requires the value to be the request's own principal: an
        # expression over request.state.principal, or a local name assigned
        # from one in the same handler.
        import ast
        import inspect
        import sys
        import textwrap

        def from_request(expr):
            return any(isinstance(n, ast.Attribute) and n.attr == "state"
                       for n in ast.walk(expr)) and "principal" in ast.unparse(expr)

        checked, bad, seen = 0, [], set()
        for route, path, _m, _d in api_auth.iter_route_declarations(_app()):
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None or not (endpoint.__module__ or "").startswith("api.") \
                    or endpoint in seen:
                continue
            seen.add(endpoint)
            module = sys.modules[endpoint.__module__]
            func = ast.parse(textwrap.dedent(inspect.getsource(endpoint))).body[0]
            names = _service_names(ast.parse(inspect.getsource(module)), module.__name__)
            from_req = {t.id for n in ast.walk(func) if isinstance(n, ast.Assign)
                        and from_request(n.value) for t in n.targets if isinstance(t, ast.Name)}
            for call, fn, passed in _principal_calls(func, names, module):
                checked += 1
                ok = passed is not None and (
                    passed is call or from_request(passed)
                    or (isinstance(passed, ast.Name) and passed.id in from_req))
                if not ok:
                    bad.append(f"{path} {endpoint.__name__} -> {fn.__qualname__}")
        assert checked > 20
        assert not bad, ("A route calls a service whose principal=None means 'auth off, "
                         "sees everything' without passing request.state.principal: %r" % bad)

    def test_services_forward_the_principal(self):
        # A service that was handed a principal must pass it on to every
        # principal-taking function it calls; a dropped one silently means
        # "auth off" for the rest of the call.
        import ast
        import importlib
        import inspect
        import pathlib
        import services

        checked, bad = 0, []
        for path in sorted(pathlib.Path(services.__file__).parent.glob("*.py")):
            name = f"services.{path.stem}"
            try:
                module = importlib.import_module(name)
            except Exception:
                continue            # e.g. a test-only guard that refuses to import
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = _service_names(tree, name)
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                args = func.args
                if "principal" not in {a.arg for a in args.posonlyargs + args.args
                                       + args.kwonlyargs}:
                    continue
                for call, fn, passed in _principal_calls(func, names, module):
                    checked += 1
                    if passed is None or (isinstance(passed, ast.Constant)
                                          and passed.value is None):
                        bad.append(f"{path.name}:{call.lineno} {func.name} -> {fn.__qualname__}")
        assert checked > 10
        assert not bad, bad

    def test_principal_call_finder_sees_every_form(self):
        import ast
        import types

        def svc(drama_id, principal=None):
            return drama_id

        module = types.SimpleNamespace(svc=svc, run=lambda fn, *a, **k: fn(*a, **k))
        src = ("def h(request, p):\n"
               "    svc(1)\n"                                   # missing
               "    svc(1, principal=None)\n"
               "    svc(1, request.state.principal)\n"          # positional
               "    run(svc, 1)\n"                              # handed off, missing
               "    run(svc, 1, principal=p)\n"
               "    svc(1, **k)\n")
        found = [(c.lineno, ast.unparse(p) if isinstance(p, ast.expr) and p is not c
                  else p is c) for c, _fn, p in _principal_calls(
                      ast.parse(src).body[0], {}, module)]
        assert found == [(2, False), (3, "None"), (4, "request.state.principal"),
                         (5, False), (6, "p"), (7, True)]

    def test_body_drama_ids_reach_a_service_that_takes_the_principal(self):
        # The path guard only sees `{drama_id}` in the URL. A drama id from
        # the request body is checked by the service, so the service must
        # take a principal (the test above then makes sure it's passed).
        # Admin-only and PC-only routes are exempt: they see everything.
        # `series_id` isn't covered: in a body it's usually a web source's
        # series id, not a library series.
        import ast
        import importlib
        import inspect
        import pathlib

        def exempt(route):
            for dec in route.decorator_list:
                for kw in getattr(dec, "keywords", ()):
                    if kw.arg != "dependencies" or not isinstance(kw.value, ast.List):
                        continue
                    for d in kw.value.elts:
                        name = getattr(d.func, "id", None) if isinstance(d, ast.Call) else None
                        if name == "local_only":
                            return True
                        if name == "require_permission" and d.args \
                                and isinstance(d.args[0], ast.Constant) \
                                and str(d.args[0].value).startswith("admin."):
                            return True
            return False

        def body_drama_arg(args):
            return any(isinstance(a, ast.Attribute) and a.attr in ("drama_id", "drama_ids")
                       for a in args)

        checked, bad = 0, []
        for path in sorted((pathlib.Path(api_auth.__file__).parent / "routers").glob("*.py")):
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

            def resolve(f):
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                        and inspect.ismodule(names.get(f.value.id)):
                    return getattr(names[f.value.id], f.attr, None)
                if isinstance(f, ast.Name):
                    return names.get(f.id)
                return None

            for route in ast.walk(tree):
                if not isinstance(route, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        or not route.decorator_list or exempt(route):
                    continue
                for node in ast.walk(route):
                    if not isinstance(node, ast.Call):
                        continue
                    fn, args = resolve(node.func), list(node.args)
                    if fn is None and getattr(node.func, "id", None) == "run_in_threadpool" \
                            and args:
                        fn, args = resolve(args[0]), args[1:]
                    if not inspect.isfunction(fn) or not body_drama_arg(
                            args + [k.value for k in node.keywords]):
                        continue
                    checked += 1
                    if "principal" not in inspect.signature(fn).parameters:
                        bad.append(f"{path.name}:{node.lineno} {fn.__qualname__}")
        assert checked >= 5
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
            svc.require_drama(world["private"], b)
        assert svc.require_drama(world["shared"], b)["id"] == world["shared"]
        assert svc.require_drama(world["private"], None)["id"] == world["private"]

    def test_tracking_into_invisible_drama_404(self, world, monkeypatch):
        from services import sources_registry_service as reg
        from services.service_errors import ConflictError, NotFoundError
        monkeypatch.setattr(reg, "require_source", lambda name: None)
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

    def test_tracked_drama_link_follows_editability(self, world):
        # The chapter check auto-imports into the linked drama, so linking
        # one is editing it: a drama B can't edit is a 404 like a missing one.
        from sources import store
        for key in ("private", "shared"):
            db.update_drama(world[key], media_type="manhua")
        store.track_series("manhuagui", "1", "One")
        client = _client(_app())

        def link(who, drama_id, series="1"):
            return client.post("/api/sources/tracked/drama", headers=world[who],
                               json={"source": "manhuagui", "series_id": series,
                                     "drama_id": drama_id})

        def stored():
            return {r["series_id"]: r["drama_id"] for r in store.list_tracked_series()}

        hidden, missing = link("b", world["private"]), link("b", 999999)
        assert hidden.status_code == missing.status_code == 404, hidden.text
        assert hidden.json()["error"]["message"].replace(str(world["private"]), "N") \
            == missing.json()["error"]["message"].replace("999999", "N")
        assert stored() == {"1": None}

        r = link("b", world["shared"])
        assert r.status_code == 200, r.text
        assert [t["drama_id"] for t in r.json()] == [world["shared"]]

        # A links their private drama; B's list hides it, and B can't
        # relink, clear or untrack it (all 404, nothing changed).
        r = link("a", world["private"])
        assert r.status_code == 200 and r.json()[0]["drama_id"] == world["private"], r.text
        assert link("b", None).status_code == 404
        assert link("b", world["shared"]).status_code == 404
        r = client.post("/api/sources/tracked", headers=world["b"],
                        json={"source": "manhuagui", "series_id": "1", "tracked": False})
        assert r.status_code == 404, r.text
        assert stored() == {"1": world["private"]}
        r = client.get("/api/sources/tracked", headers=world["b"])
        assert [t["drama_id"] for t in r.json()] == [None]

        # Admin (sees everything) and the PC owner / auth off are unchanged.
        r = link("admin", world["shared"])
        assert r.status_code == 200 and r.json()[0]["drama_id"] == world["shared"], r.text
        r = _local(_app("off")).post("/api/sources/tracked/drama",
                                     json={"source": "manhuagui", "series_id": "1",
                                           "drama_id": world["private"]})
        assert r.status_code == 200 and r.json()[0]["drama_id"] == world["private"], r.text

    def test_tracked_save_cbz_follows_the_link_too(self, world):
        # Turning on auto-save for a series linked to a drama B can't edit
        # is refused like relinking it: 404, nothing changed.
        from sources import store
        store.track_series("manhuagui", "1", "One", drama_id=world["private"])
        client = _client(_app())

        def save(who, on=True):
            return client.post("/api/sources/tracked/save-cbz", headers=world[who],
                               json={"source": "manhuagui", "series_id": "1", "save_cbz": on})

        r = save("b")
        assert r.status_code == 404, r.text
        assert r.json()["error"]["message"] == "That series isn't tracked."
        assert [t["save_cbz"] for t in store.list_tracked_series()] == [0]
        r = save("a")
        assert r.status_code == 200 and r.json()[0]["save_cbz"] is True, r.text
        assert save("b", on=False).status_code == 404
        assert [t["save_cbz"] for t in store.list_tracked_series()] == [1]

    def test_link_to_a_deleted_drama_blocks_nobody(self, world):
        from services import sources_tracking_service as tracking
        from sources import store
        store.track_series("manhuagui", "1", "One", drama_id=world["private"])
        db.delete_drama(world["private"])
        b = {"user_id": world["b_id"], "is_admin": False, "is_local_owner": False}
        assert tracking.set_tracked_drama("manhuagui", "1", None, principal=b)[0]["drama_id"] \
            is None

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
        assert row["is_private"] == 1                   # private unless shared by default

    def test_service_create_uses_share_by_default(self, world):
        from services import drama_service, ownership_service
        a = {"user_id": world["a_id"], "is_admin": False, "is_local_owner": False}
        b = {"user_id": world["b_id"], "is_admin": False, "is_local_owner": False}
        d = drama_service.create_drama(source_language="zh", title_en="Hidden", principal=a)
        row = self._row(d["id"])
        assert (row["owner_user_id"], row["is_private"]) == (world["a_id"], 1)
        assert not ownership_service.can_see_drama(b, d["id"])
        ownership_service.set_share_by_default(a, True)
        d = drama_service.create_drama(source_language="zh", title_en="Open", principal=a)
        assert self._row(d["id"])["is_private"] == 0
        assert ownership_service.can_see_drama(b, d["id"])

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
        assert (row["owner_user_id"], row["is_private"]) == (None, 1)   # private by default

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

    def test_drama_id_and_kind_only_where_visible(self, world, jobs):
        client = _client(_app())
        member = {j["job_id"]: j for j in client.get("/api/jobs", headers=world["b"]).json()["items"]}
        assert set(member) == {jobs["shared"], jobs["b_fixed"]}
        assert (member[jobs["shared"]]["drama_id"], member[jobs["shared"]]["kind"]) == (
            world["shared"], "translate")
        assert (member[jobs["b_fixed"]]["drama_id"], member[jobs["b_fixed"]]["kind"]) == (None, "other")
        # The private title's id appears nowhere in the member's responses.
        assert f'"drama_id":{world["private"]}' not in client.get(
            "/api/jobs", headers=world["b"]).text.replace(" ", "")
        assert client.get(f"/api/jobs/{jobs['priv']}", headers=world["b"]).status_code == 404
        owner = client.get(f"/api/jobs/{jobs['priv']}", headers=world["a"]).json()
        assert (owner["drama_id"], owner["kind"]) == (world["private"], "translate")

    def test_owned_by_me_agrees_with_cancel(self, world, jobs):
        # owned_by_me: the caller started the job or owns its drama. Where it
        # is true, cancel is allowed; where false, it is someone else's job
        # (a member may still cancel one on a shared drama).
        adm_drama = db.create_drama(title_en="Admin's", source_language="zh",
                                    owner_user_id=world["admin_id"], is_private=1)
        jobs = dict(jobs, adm_fixed="discover_navigation_help",
                    adm_drama=f"translate_{adm_drama}")
        db.save_job_record(jobs["adm_fixed"], "running", started_at=1.0,
                           owner_user_id=world["admin_id"])
        db.save_job_record(jobs["adm_drama"], "running", started_at=1.0, owner_user_id=None)
        household = TestClient(
            create_app(ApiSettings(household_port=8610, serve_frontend=False,
                                   google_client_id="cid", google_client_secret="s3cr3t-value",
                                   public_url=REMOTE), listener="household"),
            base_url=REMOTE.replace("https", "http"), client=("127.0.0.1", 5000),
            raise_server_exceptions=False)
        remote = _client(_app())
        # who -> (client, headers, {job key: (owned_by_me, cancel status)})
        cases = {
            "owner": (remote, world["a"], {"priv": (True, 200), "shared": (True, 200),
                                           "a_fixed": (True, 200)}),
            "member": (remote, world["b"], {"shared": (False, 200), "b_fixed": (True, 200)}),
            "remote_admin": (household, world["admin"], {
                "priv": (False, 403), "shared": (False, 200), "a_fixed": (False, 403),
                "pc_fixed": (False, 403), "b_fixed": (False, 403),
                "adm_fixed": (True, 200), "adm_drama": (True, 200)}),
            "local_owner": (_local(_app("off")), {"X-Baihe-Local": "1"},
                            {k: (True, 200) for k in jobs}),
        }
        for who, (client, headers, want) in cases.items():
            items = client.get("/api/jobs", headers=headers).json()["items"]
            assert all("owner_user_id" not in j for j in items)
            owned = {j["job_id"]: j["owned_by_me"] for j in items}
            assert owned == {jobs[k]: flag for k, (flag, _code) in want.items()}, who
            for key, job_id in jobs.items():
                one = client.get(f"/api/jobs/{job_id}", headers=headers)
                code = client.post(f"/api/jobs/{job_id}/cancel", headers=headers).status_code
                flag, expected = want.get(key, (None, 404))
                assert code == expected, (who, key, code)
                if flag is not None:
                    assert one.json()["owned_by_me"] is flag, (who, key)
                    assert not flag or code == 200, (who, key)

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
            hidden = client.get(url, headers=world["b"])
            if url.startswith("/api/live/"):
                assert hidden.status_code == 404, url
            else:
                # A shared fixed id nobody may see answers like "nothing ran":
                # 200 idle, with none of A's result or message.
                assert hidden.status_code == 200, url
                assert hidden.json() == {**hidden.json(), "job_id": "", "status": "idle",
                                         "progress": 0.0, "message": "", "result": None}, url
            assert client.get(url, headers=world["a"]).status_code == 200, url
            assert client.get(url, headers=world["admin"]).status_code == 200, url
            if not url.startswith("/api/live/"):
                assert client.get(url, headers=world["a"]).json()["status"] == "done", url
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
            # PC-only: refused for any remote caller before the owner check.
            expected = 403 if path.endswith("/delete") else 404
            if path == "/api/sources/jobs/{job_id}/result":
                # Another user's run of a shared id reads as "nothing ran".
                assert r.status_code == 200, (method, url, r.status_code)
                assert r.json()["status"] == "idle" and r.json()["result"] is None
                assert client.get(url, headers=world["a"]).json()["status"] == "done"
                continue
            assert r.status_code == expected, (method, url, r.status_code)
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


class TestLibrarySharingFlags:
    """The Library list tells the client each item's private flag and whether
    the viewer created it, so the sharing control needs no extra call."""

    def test_drama_flags(self, world):
        w = world
        client = _client(_app())
        items = {d["id"]: d for d in client.get("/api/library/dramas", headers=w["a"]).json()["items"]}
        assert items[w["private"]]["is_private"] is True
        assert items[w["private"]]["owned_by_me"] is True
        assert items[w["shared"]]["is_private"] is False
        assert items[w["in_pseries"]]["is_private"] is True      # follows its series
        b_items = {d["id"]: d for d in client.get("/api/library/dramas", headers=w["b"]).json()["items"]}
        assert b_items[w["shared"]]["owned_by_me"] is False
        off = _local(_app("off")).get("/api/library/dramas").json()["items"]
        assert all(d["owned_by_me"] is False for d in off)
        detail = client.get(f"/api/library/dramas/{w['shared']}", headers=w["a"]).json()
        assert detail["is_private"] is None

    def test_series_flags(self, world):
        w = world
        db.create_drama(title_en="Second", source_language="zh", series_id=w["pseries"],
                        owner_user_id=w["a_id"])
        client = _client(_app())
        mine = client.get("/api/library/series", headers=w["a"]).json()["items"]
        assert mine[0]["is_private"] is True and mine[0]["owned_by_me"] is True
        admin = client.get("/api/library/series", headers=w["admin"]).json()["items"]
        assert admin[0]["owned_by_me"] is False
