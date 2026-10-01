"""
Step 38: Benchmark Lab routes (api/routers/benchmark_routes.py) over
services/benchmark_lab_service.py -- the HTTP layer only. The service's own
behaviour (scoring, import parsing, cap refusals, arena alignment) is in
tests/test_benchmark_lab.py; this file pins who may call which route, the
confirm gates, request-schema strictness, status codes, and that no key
ever leaves the server.

FastAPI TestClient against an isolated library. Runs use the free
`fake` engine or a fake class patched into
translate_engines.ENGINES; no network, no real keys, no models.
"""

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service, settings_service, translate_service
from services import benchmark_lab_service as svc

BASE = "/api/benchmark"
REMOTE = "https://baihe.example.com"
SECRET = "sk-ant-SECRETSECRETSECRET1234567890"
SECRET_CORE = "SECRETSECRETSECRET1234567890"


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


# ---- clients / sessions ------------------------------------------------------

def _app(auth="off"):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False))


def _local(auth="off"):
    """A direct loopback connection (the owner at the PC)."""
    return TestClient(_app(auth), base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _remote(auth="on"):
    return TestClient(_app(auth), base_url=REMOTE, raise_server_exceptions=False)


def _headers(session):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
            api_auth.CSRF_HEADER: session["csrf_token"]}


def _household(email="kid@example.com"):
    """A non-admin user holding every household and opt-in permission."""
    u = auth_service.add_user(email)
    for p in auth_service.OPT_IN_PERMISSIONS:
        auth_service.grant_permission(u["id"], p)
    return _headers(auth_service.create_session(u["id"], "pytest", "203.0.113.9"))


def _admin(email="admin@example.com"):
    u = auth_service.grant_admin_local(email)
    return _headers(auth_service.create_session(u["id"], "pytest", "203.0.113.9"))


def _wait(job_id=svc.JOB_ID, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


def _case(label="c", source="你好", ref="Hello", set_name="s"):
    return svc.create_case(label, source, ref, set_name=set_name)["id"]


def _drama_line():
    did = db.create_drama(title_en="D", source_language="zh")
    db.save_lines(did, [Line(0, 0.0, 1.0, "你好", "Hi there")])
    return did, db.load_lines(did)[0]["id"]


def _session_count():
    return len(db.list_benchmark_sessions(limit=1000))


# What the React client (frontend/src/api/pcOnly.ts) sends on every PC-only
# write; a body-less POST needs it to pass local_only()'s cross-site rule.
LOCAL_HDR = {"X-Baihe-Local": "1"}


def _case_count():
    return len(db.list_benchmark_cases())


RUN_OK = {"stage": "translation", "configs": [{"engine": "fake"}], "confirm": True}

# (method, path, kwargs) for every read route.
_READS = [
    ("get", "/options", {}),
    ("get", "/cases", {}),
    ("get", "/sets", {}),
    ("get", "/runs", {}),
    ("post", "/estimate", {"json": {"configs": [{"engine": "fake"}]}}),
]


# ---- reads: admin.diagnostics -------------------------------------------------

class TestReadPermissions:
    @pytest.mark.parametrize("method,path,kw", _READS)
    def test_auth_off_loopback_200(self, isolated_db, method, path, kw):
        _case()
        r = getattr(_local("off"), method)(BASE + path, **kw)
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("method,path,kw", _READS)
    def test_auth_on_no_session_401(self, isolated_db, method, path, kw):
        _case()
        r = getattr(_remote(), method)(BASE + path, **kw)
        assert r.status_code == 401, r.text

    @pytest.mark.parametrize("method,path,kw", _READS)
    def test_auth_on_household_403(self, isolated_db, method, path, kw):
        _case()
        r = getattr(_remote(), method)(BASE + path, headers=_household(), **kw)
        assert r.status_code == 403, r.text

    @pytest.mark.parametrize("method,path,kw", _READS)
    def test_auth_on_admin_200(self, isolated_db, method, path, kw):
        _case()
        r = getattr(_remote(), method)(BASE + path, headers=_admin(), **kw)
        assert r.status_code == 200, r.text

    def test_run_detail_and_arena_follow_the_same_rule(self, isolated_db):
        """GET /runs/{id} and /arena need data first, so they're checked
        together here: household 403, admin 200, auth-off loopback 200."""
        _case()
        ids = []
        for _ in range(2):
            r = _local("off").post(f"{BASE}/runs", json=RUN_OK)
            assert r.status_code == 200, r.text
            assert _wait()["status"] == "done"
            ids += r.json()["session_ids"]
        a, b = ids
        paths = [f"/runs/{a}", f"/arena?run_ids={a}&run_ids={b}"]
        remote = _remote()
        hh, adm = _household(), _admin()
        for p in paths:
            assert remote.get(BASE + p, headers=hh).status_code == 403, p
            assert remote.get(BASE + p).status_code == 401, p
            assert remote.get(BASE + p, headers=adm).status_code == 200, p
            assert _local("off").get(BASE + p).status_code == 200, p

    def test_household_estimate_refused_before_validation(self, isolated_db):
        """A caller without the permission gets the generic 403, not a 422
        that would describe the body."""
        r = _remote().post(f"{BASE}/estimate", headers=_household(), json={"bogus": 1})
        assert r.status_code == 403


# ---- writes: local_only -------------------------------------------------------

def _write_routes(case_id, drama_id, line_id):
    return [
        ("/cases", {"label": "x", "source_text": "谢谢", "reference_text": "Thanks"}),
        (f"/cases/{case_id}/delete", {"confirm": True}),
        ("/import", {"set_name": "imp", "text": "再见\tBye\n", "format": "tsv"}),
        (f"/dramas/{drama_id}/lines/{line_id}/regression", None),
        ("/runs", RUN_OK),
    ]


class TestWritesLocalOnly:
    def test_remote_admin_refused_nothing_written(self, isolated_db):
        case_id = _case()
        did, line_id = _drama_line()
        c, h = _remote(), _admin()
        for path, body in _write_routes(case_id, did, line_id):
            kw = {"json": body} if body is not None else {}
            r = c.post(BASE + path, headers={**h, **LOCAL_HDR}, **kw)
            assert r.status_code == 403, (path, r.text)
        assert [x["id"] for x in db.list_benchmark_cases()] == [case_id]
        assert _session_count() == 0
        assert svc.list_cases(tier="regression")["cases"] == []
        assert background_jobs.get_status(svc.JOB_ID) is None

    @pytest.mark.parametrize("auth", ["on", "off"])
    def test_loopback_writes_work(self, isolated_db, auth):
        c = _local(auth)
        r = c.post(f"{BASE}/cases", json={"label": "one", "source_text": "谢谢",
                                          "reference_text": "Thanks", "set_name": "mine"})
        assert r.status_code == 200, r.text
        created = r.json()
        assert (created["label"], created["tier"], created["set_name"]) == \
            ("one", "application", "mine")
        assert created["has_reference"] is True

        r = c.post(f"{BASE}/import", json={"set_name": "imp", "format": "tsv",
                                           "text": "再见\tBye\n早\n"})
        assert r.status_code == 200, r.text
        assert (r.json()["added"], r.json()["skipped"]) == (2, 0)
        assert r.json()["tier"] == "public"

        did, line_id = _drama_line()
        r = c.post(f"{BASE}/dramas/{did}/lines/{line_id}/regression", headers=LOCAL_HDR)
        assert r.status_code == 200, r.text
        assert r.json()["case"]["tier"] == "regression"
        assert r.json()["case"]["origin_line_id"] == line_id
        assert r.json()["replaced"] is False

        r = c.post(f"{BASE}/cases/{created['id']}/delete", json={"confirm": True})
        assert r.status_code == 200, r.text
        assert r.json() == {"deleted": True, "id": created["id"]}
        assert db.get_benchmark_case(created["id"]) is None

        r = c.post(f"{BASE}/runs", json=RUN_OK)
        assert r.status_code == 200, r.text
        assert _wait()["status"] == "done"

    @pytest.mark.parametrize("auth", ["on", "off"])
    def test_bare_bodyless_post_refused_cross_site(self, isolated_db, auth):
        """A body-less POST with neither JSON nor X-Baihe-Local is a CORS
        "simple" request another local page could send: refused, nothing
        added (api/auth.py _cross_site_safe)."""
        did, line_id = _drama_line()
        r = _local(auth).post(f"{BASE}/dramas/{did}/lines/{line_id}/regression")
        assert r.status_code == 403, r.text
        assert _case_count() == 0

    def test_duplicate_case_409(self, isolated_db):
        c = _local()
        body = {"label": "one", "source_text": "谢谢", "set_name": "s"}
        assert c.post(f"{BASE}/cases", json=body).status_code == 200
        r = c.post(f"{BASE}/cases", json={**body, "label": "two"})
        assert r.status_code == 409, r.text
        assert _case_count() == 1

    def test_bad_import_422_nothing_added(self, isolated_db):
        r = _local().post(f"{BASE}/import", json={"set_name": "s", "text": "not json\n"})
        assert r.status_code == 422, r.text
        assert _case_count() == 0

    def test_regression_unknown_line_404(self, isolated_db):
        did, _ = _drama_line()
        r = _local().post(f"{BASE}/dramas/{did}/lines/999999/regression", headers=LOCAL_HDR)
        assert r.status_code == 404, r.text
        assert _case_count() == 0

    def test_regression_unknown_drama_404(self, isolated_db):
        r = _local().post(f"{BASE}/dramas/999999/lines/1/regression", headers=LOCAL_HDR)
        assert r.status_code == 404, r.text


# ---- confirm gates ------------------------------------------------------------

class TestConfirm:
    @pytest.mark.parametrize("body", [
        {"stage": "translation", "configs": [{"engine": "fake"}]},
        {"stage": "translation", "configs": [{"engine": "fake"}], "confirm": False},
    ])
    def test_run_without_confirm_422_no_row(self, isolated_db, body):
        _case()
        r = _local().post(f"{BASE}/runs", json=body)
        assert r.status_code == 422, r.text
        assert _error(r)["code"] == "validation_error"
        assert _session_count() == 0
        assert background_jobs.get_status(svc.JOB_ID) is None

    @pytest.mark.parametrize("body", [{}, {"confirm": False}])
    def test_delete_case_without_confirm_422_kept(self, isolated_db, body):
        case_id = _case()
        r = _local().post(f"{BASE}/cases/{case_id}/delete", json=body)
        assert r.status_code == 422, r.text
        assert db.get_benchmark_case(case_id) is not None

    def test_delete_unknown_case_404(self, isolated_db):
        r = _local().post(f"{BASE}/cases/999999/delete", json={"confirm": True})
        assert r.status_code == 404, r.text

    def test_estimate_writes_nothing(self, isolated_db):
        _case()
        r = _local().post(f"{BASE}/estimate", json={"configs": [{"engine": "fake"}]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["case_count"] == 1 and body["estimated_cost_usd"] == 0
        assert _session_count() == 0
        assert background_jobs.get_status(svc.JOB_ID) is None


# ---- runs, results, arena -----------------------------------------------------

class TestRuns:
    def test_run_then_results(self, isolated_db):
        case_id = _case()
        c = _local()
        r = c.post(f"{BASE}/runs", json={**RUN_OK, "label": "base", "prompt_version": "v1"})
        assert r.status_code == 200, r.text
        started = r.json()
        assert started["job_id"] == "benchmark_lab"
        assert len(started["session_ids"]) == 1 and started["arena_group"] is None
        assert _wait()["status"] == "done"
        (sid,) = started["session_ids"]

        r = c.get(f"{BASE}/runs/{sid}")
        assert r.status_code == 200, r.text
        detail = r.json()
        run = detail["run"]
        assert (run["id"], run["engine"], run["status"], run["label"], run["prompt_version"]) == \
            (sid, "fake", "done", "base", "v1")
        assert run["case_count"] == 1
        (res,) = detail["results"]
        assert res["case_id"] == case_id
        assert res["output_text"].startswith("[TEST]")
        assert res["metric"] == "similarity"
        assert res["scorer"] == "builtin"

        listed = c.get(f"{BASE}/runs").json()["runs"]
        assert [x["id"] for x in listed] == [sid]

    def test_run_with_no_cases_refused_no_row(self, isolated_db):
        r = _local().post(f"{BASE}/runs", json=RUN_OK)
        assert r.status_code == 400, r.text
        assert _session_count() == 0

    def test_run_while_another_running_409_no_row(self, isolated_db):
        _case()
        with background_jobs._lock:
            background_jobs._jobs[svc.JOB_ID] = {
                "status": "running", "progress": 0.0, "message": "", "result": None,
                "error": None, "started_at": time.time(), "finished_at": None}
        r = _local().post(f"{BASE}/runs", json=RUN_OK)
        assert r.status_code == 409, r.text
        assert _session_count() == 0

    def test_unknown_engine_422(self, isolated_db):
        _case()
        r = _local().post(f"{BASE}/runs", json={**RUN_OK, "configs": [{"engine": "nope"}]})
        assert r.status_code == 422, r.text
        assert _session_count() == 0

    def test_two_configs_is_an_arena(self, isolated_db, monkeypatch):
        class Echo:
            name = "claude"
            supports_reference = True

            def __init__(self, api_key=None, model="claude-sonnet-5", **kw):
                self.model = model
                self.last_usage = {"input_tokens": 0, "output_tokens": 0}

            def translate_batch(self, zh_lines, context):
                return ["Hello" for _ in zh_lines]
        monkeypatch.setitem(translate_engines.ENGINES, "claude", Echo)
        monkeypatch.setattr(translate_service, "resolve_api_key",
                            lambda name, env_path=None: "fake-key" if name == "claude" else "offline")
        _case()
        c = _local()
        r = c.post(f"{BASE}/runs", json={**RUN_OK, "configs": [{"engine": "claude"},
                                                                {"engine": "fake"}]})
        assert r.status_code == 200, r.text
        started = r.json()
        assert started["arena_group"]
        a, b = started["session_ids"]
        assert _wait()["status"] == "done"

        r = c.get(f"{BASE}/arena", params={"run_ids": [a, b]})
        assert r.status_code == 200, r.text
        view = r.json()
        assert [x["id"] for x in view["runs"]] == [a, b]
        assert all(x["arena_group"] == started["arena_group"] for x in view["runs"])
        (row,) = view["rows"]
        assert len(row["results"]) == 2
        assert row["results"][0]["output_text"] == "Hello"
        assert row["results"][1]["output_text"].startswith("[TEST]")
        assert "fake-key" not in r.text

    def test_unknown_run_404(self, isolated_db):
        r = _local().get(f"{BASE}/runs/999999")
        assert r.status_code == 404, r.text
        assert _error(r)["code"] == "not_found"

    @pytest.mark.parametrize("query", ["run_ids=1", "", "run_ids=1&run_ids=2&run_ids=3"
                                       "&run_ids=4&run_ids=5"])
    def test_arena_needs_two_to_four_422(self, isolated_db, query):
        r = _local().get(f"{BASE}/arena?{query}")
        assert r.status_code == 422, r.text

    def test_arena_unknown_runs_404(self, isolated_db):
        r = _local().get(f"{BASE}/arena?run_ids=999998&run_ids=999999")
        assert r.status_code == 404, r.text

    def test_arena_same_run_twice_422(self, isolated_db):
        _case()
        c = _local()
        (sid,) = c.post(f"{BASE}/runs", json=RUN_OK).json()["session_ids"]
        _wait()
        r = c.get(f"{BASE}/arena?run_ids={sid}&run_ids={sid}")
        assert r.status_code == 422, r.text

    @pytest.mark.parametrize("path", ["/runs/0", "/runs/-1", "/runs/abc"])
    def test_bad_run_id_422(self, isolated_db, path):
        assert _local().get(BASE + path).status_code == 422

    @pytest.mark.parametrize("query", ["stage=nope", "limit=0", "limit=201"])
    def test_runs_list_bad_query_422(self, isolated_db, query):
        assert _local().get(f"{BASE}/runs?{query}").status_code == 422


# ---- schema strictness ---------------------------------------------------------

class TestExtraFields:
    @pytest.mark.parametrize("path,body", [
        ("/cases", {"label": "x", "source_text": "谢谢", "extra": 1}),
        ("/cases/{cid}/delete", {"confirm": True, "extra": 1}),
        ("/import", {"set_name": "s", "text": "a\tb\n", "format": "tsv", "extra": 1}),
        ("/runs", {**RUN_OK, "extra": 1}),
        ("/runs", {**RUN_OK, "configs": [{"engine": "fake", "api_key": SECRET}]}),
        ("/estimate", {"configs": [{"engine": "fake"}], "extra": 1}),
        ("/estimate", {"configs": [{"engine": "fake", "api_key": SECRET}]}),
    ])
    def test_extra_body_field_422_nothing_written(self, isolated_db, path, body):
        cid = _case()
        r = _local().post(BASE + path.format(cid=cid), json=body)
        assert r.status_code == 422, r.text
        assert SECRET_CORE not in r.text
        assert [x["id"] for x in db.list_benchmark_cases()] == [cid]
        assert _session_count() == 0

    @pytest.mark.parametrize("path,body", [
        ("/cases", {"label": "x", "source_text": "谢谢", "tier": "gold"}),
        ("/cases", {"label": "x", "source_text": "谢谢", "source_language": "en"}),
        ("/import", {"set_name": "s", "text": "a", "format": "csv"}),
        ("/runs", {**RUN_OK, "stage": "dubbing"}),
        ("/runs", {**RUN_OK, "configs": []}),
        ("/runs", {**RUN_OK, "configs": [{"engine": "fake"}] * 5}),
    ])
    def test_out_of_range_values_422(self, isolated_db, path, body):
        _case()
        r = _local().post(BASE + path, json=body)
        assert r.status_code == 422, r.text
        assert _case_count() == 1 and _session_count() == 0


# ---- keys never leave the server -----------------------------------------------

class TestNoKeyInResponses:
    def test_options_has_key_flags_only(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda key, env_path=None: SECRET if key == "claude" else None)
        r = _local().get(f"{BASE}/options")
        assert r.status_code == 200, r.text
        assert SECRET_CORE not in r.text
        engines = {e["name"]: e for e in r.json()["translation_engines"]}
        assert set(engines) == set(translate_engines.ENGINES)
        for e in engines.values():
            assert set(e) == {"name", "label", "free", "models", "key_configured"}
            assert isinstance(e["key_configured"], bool)
        assert engines["claude"]["key_configured"] is True
        assert engines["deepseek"]["key_configured"] is False
        assert engines["fake"]["key_configured"] is True

    @pytest.mark.parametrize("where", ["translate", "init"])
    def test_engine_error_with_key_is_redacted(self, isolated_db, monkeypatch, where):
        class Leaky:
            name = "claude"
            supports_reference = True

            def __init__(self, api_key=None, model="claude-sonnet-5", **kw):
                if where == "init":
                    raise RuntimeError(f"auth failed for key {api_key}")
                self.api_key = api_key
                self.model = model
                self.last_usage = {}

            def translate_batch(self, zh_lines, context):
                raise RuntimeError(f"401 Unauthorized: invalid x-api-key {self.api_key}")
        monkeypatch.setitem(translate_engines.ENGINES, "claude", Leaky)
        monkeypatch.setattr(translate_service, "resolve_api_key",
                            lambda name, env_path=None: SECRET if name == "claude" else "offline")
        _case()
        c = _local()
        r = c.post(f"{BASE}/runs", json={**RUN_OK, "configs": [{"engine": "claude"},
                                                                {"engine": "fake"}]})
        assert r.status_code == 200, r.text
        assert SECRET_CORE not in r.text
        assert _wait()["status"] == "done"
        a, b = r.json()["session_ids"]

        detail = c.get(f"{BASE}/runs/{a}")
        assert detail.status_code == 200
        if where == "translate":
            assert detail.json()["run"]["error_count"] == 1
            assert detail.json()["results"][0]["error"]
        else:
            assert detail.json()["run"]["status"] == "failed"
            assert detail.json()["run"]["note"]
        for resp in (detail, c.get(f"{BASE}/runs"), c.get(f"{BASE}/arena?run_ids={a}&run_ids={b}"),
                     c.get(f"{BASE}/options"), c.get(f"{BASE}/cases"),
                     c.post(f"{BASE}/estimate", json={"configs": [{"engine": "claude"}]})):
            assert resp.status_code == 200, resp.text
            assert SECRET_CORE not in resp.text, resp.request.url
        # Nor in the stored rows the routes read from.
        assert SECRET_CORE not in str(db.list_benchmark_sessions(limit=1000))
        assert SECRET_CORE not in str(db.list_benchmark_results(a))

    def test_missing_key_503_no_row(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
        _case()
        r = _local().post(f"{BASE}/runs", json={**RUN_OK, "configs": [{"engine": "claude"}]})
        assert r.status_code == 503, r.text
        assert _session_count() == 0
