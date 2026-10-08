"""
Benchmark Lab routes added with the reviewed-title set builder and the LLM
judge: who may call them, request strictness and what the responses carry.
The services' behaviour is in tests/test_benchmark_set_builder.py and
tests/test_benchmark_judge.py.
"""
import json
import re
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service, translate_service
from services import benchmark_lab_service as lab

BASE = "/api/benchmark"
LOCAL_HDR = {"X-Baihe-Local": "1"}
SECRET = "sk-ant-SECRETSECRETSECRET1234567890"


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _local(auth="off"):
    app = create_app(ApiSettings(auth_mode=auth, serve_frontend=False))
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _title(count=4):
    did = db.create_drama(title_en="D", source_language="zh")
    db.save_lines(did, [Line(i, float(i), i + 0.9, f"第{i}句", f"Line {i}") for i in range(count)])
    return did


def _wait(timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(lab.JOB_ID)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


class _Tested:
    def __init__(self, api_key=None, model="claude-sonnet-5-5", **kw):
        self.model = model
        self.last_usage = {}

    def translate_batch(self, zh_lines, context):
        return ["good" for _ in zh_lines]


class _Judge:
    def __init__(self, api_key=None, model="deepseek-v4-flash", **kw):
        self.model = model


@pytest.fixture
def engines(monkeypatch):
    monkeypatch.setitem(translate_engines.ENGINES, "claude", _Tested)
    monkeypatch.setitem(translate_engines.ENGINES, "deepseek", _Judge)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda n, env_path=None: SECRET)

    def fake_llm(engine, prompt, max_tokens=0, fallback="", usage_cb=None):
        ids = re.findall(r"^\[(\d+)\]", prompt, flags=re.M)
        return json.dumps({i: {"accuracy": 1, "tone": 1, "naturalness": 1} for i in ids})

    monkeypatch.setattr(translate_engines, "call_llm_json", fake_llm)


class TestBuildFromTitle:
    def test_loopback_builds_and_dry_run_only_counts(self, isolated_db):
        did = _title()
        c = _local()
        body = {"drama_id": did, "set_name": "mine", "include": "all"}
        dry = c.post(f"{BASE}/sets/from-title", json={**body, "dry_run": True})
        assert dry.status_code == 200, dry.text
        assert dry.json()["case_count"] == 1 and db.list_benchmark_cases() == []
        r = c.post(f"{BASE}/sets/from-title", json=body)
        assert r.json()["added"] == 1
        assert c.get(f"{BASE}/sets").json()["sets"][0]["set_name"] == "mine"

    def test_remote_is_refused_and_writes_nothing(self, isolated_db):
        did = _title()
        app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
        remote = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
        admin = auth_service.grant_admin_local("admin@example.com")
        session = auth_service.create_session(admin["id"], "pytest", "203.0.113.9")
        from api import auth as api_auth
        headers = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
                   api_auth.CSRF_HEADER: session["csrf_token"], **LOCAL_HDR}
        r = remote.post(f"{BASE}/sets/from-title", headers=headers,
                        json={"drama_id": did, "set_name": "mine", "include": "all"})
        assert r.status_code == 403
        assert db.list_benchmark_cases() == []

    @pytest.mark.parametrize("body", [
        {"drama_id": 1, "set_name": "", "include": "all"},
        {"drama_id": 1, "set_name": "s", "include": "everything"},
        {"drama_id": 1, "set_name": "s", "lines_per_case": 11},
        {"drama_id": 1, "set_name": "s", "path": "/etc/passwd"},
    ])
    def test_bad_requests_are_422(self, isolated_db, body):
        assert _local().post(f"{BASE}/sets/from-title", json=body).status_code == 422

    def test_nothing_reviewed_is_a_clear_error(self, isolated_db):
        did = _title()
        r = _local().post(f"{BASE}/sets/from-title", json={"drama_id": did, "set_name": "s"})
        assert r.status_code == 422
        assert "all lines" in r.json()["error"]["message"]

    def test_unknown_title_is_404(self, isolated_db):
        r = _local().post(f"{BASE}/sets/from-title", json={"drama_id": 99, "set_name": "s"})
        assert r.status_code == 404


class TestJudgeOverHttp:
    def _body(self, judge, **kw):
        return {"stage": "translation", "configs": [{"engine": "claude"}], "judge": judge, **kw}

    def test_estimate_shows_the_judge_part(self, isolated_db, engines):
        lab.create_case("c", "你好", "hi", set_name="s")
        r = _local().post(f"{BASE}/estimate", json=self._body({"engine": "deepseek"}))
        assert r.status_code == 200, r.text
        j = r.json()["judge"]
        assert j["engine"] == "deepseek" and j["estimated_cost_usd"] > 0 and j["warning"] is None

    def test_same_model_judge_is_refused_until_allowed(self, isolated_db, engines):
        lab.create_case("c", "你好", "hi", set_name="s")
        c = _local()
        r = c.post(f"{BASE}/estimate", json=self._body({"engine": "claude"}))
        assert r.status_code == 400 and "same engine and model" in r.json()["error"]["message"]
        ok = c.post(f"{BASE}/estimate", json=self._body({"engine": "claude", "allow_same_model": True}))
        assert ok.status_code == 200 and ok.json()["judge"]["warning"]

    def test_run_with_judge_returns_scores_on_runs_results_and_arena(self, isolated_db, engines):
        lab.create_case("c", "你好", "good", set_name="s")
        c = _local()
        r = c.post(f"{BASE}/runs", json=self._body({"engine": "deepseek"}, confirm=True),
                   headers=LOCAL_HDR)
        assert r.status_code == 200, r.text
        _wait()
        sid = r.json()["session_ids"][0]
        runs = c.get(f"{BASE}/runs").json()["runs"]
        assert runs[0]["judge"]["status"] == "done" and runs[0]["judge"]["average"]["overall"] == 1.0
        detail = c.get(f"{BASE}/runs/{sid}").json()
        assert detail["results"][0]["judge"]["accuracy"] == 1.0
        assert detail["results"][0]["score"] is not None
        assert SECRET not in json.dumps(detail) and "SECRETSECRET" not in json.dumps(runs)

    def test_a_judge_on_a_non_translation_run_is_refused(self, isolated_db, engines):
        r = _local().post(f"{BASE}/estimate", json={"stage": "ocr", "configs": [{"engine": "tesseract"}],
                                                    "judge": {"engine": "deepseek"}})
        assert r.status_code == 422

    def test_judge_has_no_extra_fields(self, isolated_db, engines):
        r = _local().post(f"{BASE}/estimate", json=self._body({"engine": "deepseek", "api_key": "x"}))
        assert r.status_code == 422
