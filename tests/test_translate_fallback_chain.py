"""Step 97b: translate fallback chain. Fully mocked: fake engines, no network."""
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api.server import create_app
from core import Line
from services import translate_run_service as svc, translate_service
from services.service_errors import InvalidInputError


class RateLimitError(Exception):
    status_code = 429


class AuthError(Exception):
    status_code = 401


class Fake:
    """Translates every line to 'EN:<zh>' unless `fail` is set."""
    fail = None
    calls = 0

    def __init__(self, api_key=None, model="m", **kw):
        self.model = model
        self.last_usage = {}

    def translate_batch(self, zh_lines, context):
        type(self).calls += 1
        if type(self).fail:
            raise type(self).fail
        self.last_usage = {"input_tokens": 1000000, "output_tokens": 0}
        return [f"{self.name}:{z}" for z in zh_lines]


def _make(name):
    return type(name.title(), (Fake,), {"name": name, "fail": None, "calls": 0})


@pytest.fixture
def engines(monkeypatch):
    background_jobs.clear_all_jobs()
    made = {n: _make(n) for n in ("claude", "deepseek", "deepl", "google")}
    for n, cls in made.items():
        monkeypatch.setitem(translate_engines.ENGINES, n, cls)
    monkeypatch.setattr(translate_service, "_resolve_api_key", lambda n: "k")
    monkeypatch.setattr(svc, "_summary_engine", lambda: (None, None))
    yield made
    background_jobs.clear_all_jobs()


def _seed(n=3):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"z{i}", en="") for i in range(n)])
    return did


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_is_fallback_error_classification():
    assert translate_engines.is_fallback_error(RateLimitError())
    assert translate_engines.is_fallback_error(AuthError())
    assert translate_engines.is_fallback_error(TimeoutError("x"))
    assert translate_engines.is_fallback_error(type("ReadTimeout", (Exception,), {})())
    assert translate_engines.is_fallback_error(type("APIConnectionError", (Exception,), {})())
    assert not translate_engines.is_fallback_error(RuntimeError("bug"))
    assert not translate_engines.is_fallback_error(
        translate_engines.ContentModerationBlocked("claude", "policy"))


def test_falls_back_on_rate_limit_and_stays_on_fallback(isolated_db, engines):
    engines["claude"].fail = RateLimitError("429")
    did = _seed()
    out = svc.start_translate_run(did, engine_name="claude", batch_size=1,
                                  fallback_chain=[{"engine": "deepseek"}])
    assert out["fallback_engines"] == ["deepseek"]
    job = _wait(out["job_id"])
    assert job["status"] == "done"
    assert [r["en"] for r in db.load_lines(did)] == ["deepseek:z0", "deepseek:z1", "deepseek:z2"]
    assert engines["claude"].calls == 1  # sticky: primary not retried per batch
    fb = job["result"]["fallbacks"]
    assert fb[0]["from"] == "claude" and fb[0]["to"] == "deepseek"


def test_no_fallback_on_generic_error(isolated_db, engines, monkeypatch):
    monkeypatch.setattr(translate_engines, "time", SimpleNamespace(sleep=lambda s: None))
    engines["claude"].fail = RuntimeError("real bug")
    did = _seed(1)
    out = svc.start_translate_run(did, engine_name="claude",
                                  fallback_chain=[{"engine": "deepseek"}])
    _wait(out["job_id"])
    assert engines["deepseek"].calls == 0
    assert db.load_lines(did)[0]["en"] == ""


def test_no_fallback_on_content_moderation(isolated_db, engines, monkeypatch):
    monkeypatch.setattr(translate_engines, "time", SimpleNamespace(sleep=lambda s: None))
    engines["claude"].fail = translate_engines.ContentModerationBlocked("claude", "policy")
    did = _seed(1)
    _wait(svc.start_translate_run(did, engine_name="claude",
                                  fallback_chain=[{"engine": "deepseek"}])["job_id"])
    assert engines["deepseek"].calls == 0
    assert db.load_lines(did)[0]["flag"] == "content_blocked"


def test_chain_must_not_cross_engine_class_or_repeat(isolated_db, engines):
    did = _seed(1)
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, engine_name="claude", fallback_chain=[{"engine": "deepl"}])
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, engine_name="deepl", fallback_chain=[{"engine": "claude"}])
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, engine_name="claude", fallback_chain=[{"engine": "claude"}])
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, engine_name="claude", fallback_chain=[{"engine": "nope"}])


def test_translation_only_chain_allowed(isolated_db, engines):
    engines["deepl"].fail = AuthError("401")
    did = _seed(1)
    _wait(svc.start_translate_run(did, engine_name="deepl",
                                  fallback_chain=[{"engine": "google"}])["job_id"])
    assert db.load_lines(did)[0]["en"] == "google:z0"


def test_each_engine_has_its_own_cap_and_spend(isolated_db, engines):
    a, b = engines["claude"](model="m"), engines["deepseek"](model="m")
    fe = translate_engines.FallbackEngine([a, b], ["claude", "deepseek"], [1.0, 1.0])
    a.translate_batch = lambda z, c: (_ for _ in ()).throw(RateLimitError("429"))
    b.last_usage = {}
    fe.spent[0] = 5.0
    assert fe.cap_exhausted()
    fe.translate_batch(["z"], {})
    assert fe.active == 1 and not fe.cap_exhausted()  # b's own spend is 0


def test_failure_of_last_engine_raises(isolated_db, engines):
    a, b = engines["claude"](), engines["deepseek"]()
    fe = translate_engines.FallbackEngine([a, b], ["claude", "deepseek"])
    engines["claude"].fail = RateLimitError("429")
    engines["deepseek"].fail = AuthError("401 sk-ant-SECRETSECRETSECRET1234")
    with pytest.raises(AuthError):
        fe.translate_batch(["z"], {})
    assert "SECRETSECRET" not in repr(fe.events)


def test_api_accepts_fallback_chain_and_rejects_bad_shape(isolated_db, engines):
    client = TestClient(create_app())
    did = _seed(1)
    url = f"/api/translate-run/dramas/{did}/run"
    assert client.post(url, json={"engine": "claude", "fallback_chain": [
        {"engine": "deepseek", "key": "x"}]}).status_code == 422
    assert client.post(url, json={"engine": "claude", "fallback_chain": [
        {"engine": "deepl"}]}).status_code == 422
    r = client.post(url, json={"engine": "claude", "fallback_chain": [{"engine": "deepseek"}]})
    assert r.status_code == 200 and r.json()["fallback_engines"] == ["deepseek"]
    _wait(r.json()["job_id"])
