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


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(translate_engines, "_fallback_sleep", slept.append)
    return slept


def _make(name):
    return type(name.title(), (Fake,), {"name": name, "fail": None, "calls": 0})


@pytest.fixture
def engines(monkeypatch):
    background_jobs.clear_all_jobs()
    made = {n: _make(n) for n in ("claude", "deepseek", "deepl", "google")}
    for n, cls in made.items():
        monkeypatch.setitem(translate_engines.ENGINES, n, cls)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda n: "k")
    monkeypatch.setattr(svc, "_summary_engine", lambda *a, **k: (None, None))
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
    # primary retried with backoff (1 + retries) once, then sticky: not retried per batch
    assert engines["claude"].calls == 1 + translate_engines.FALLBACK_TRANSIENT_RETRIES
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


class _Flaky:
    """Fails with `exc` for the first `n_fail` calls, then succeeds."""
    name = "flaky"
    last_usage = {}

    def __init__(self, exc, n_fail):
        self.exc, self.n_fail, self.calls = exc, n_fail, 0

    def translate_batch(self, zh_lines, context):
        self.calls += 1
        if self.calls <= self.n_fail:
            raise self.exc
        return [f"ok:{z}" for z in zh_lines]


class _TimeoutError(Exception):
    pass


def test_transient_error_retries_primary_with_backoff_before_switching(_no_sleep):
    a, b = _Flaky(RateLimitError("429"), 2), _Flaky(AuthError("x"), 0)
    fe = translate_engines.FallbackEngine([a, b], ["claude", "deepseek"])
    assert fe.translate_batch(["z"], {}) == ["ok:z"]
    assert fe.active == 0 and fe.events == [] and a.calls == 3 and b.calls == 0
    assert _no_sleep == [1.0, 2.0]


def test_transient_error_switches_after_retries_exhausted(_no_sleep):
    a, b = _Flaky(_TimeoutError("t"), 99), _Flaky(AuthError("x"), 0)
    fe = translate_engines.FallbackEngine([a, b], ["claude", "deepseek"])
    assert fe.translate_batch(["z"], {}) == ["ok:z"]
    assert a.calls == 1 + translate_engines.FALLBACK_TRANSIENT_RETRIES
    assert fe.active == 1 and len(fe.events) == 1 and len(_no_sleep) == 2


def test_auth_error_switches_immediately_without_sleep(_no_sleep):
    a, b = _Flaky(AuthError("401"), 99), _Flaky(AuthError("x"), 0)
    fe = translate_engines.FallbackEngine([a, b], ["claude", "deepseek"])
    fe.translate_batch(["z"], {})
    assert a.calls == 1 and fe.active == 1 and _no_sleep == []


def test_retry_budget_resets_for_next_engine(_no_sleep):
    a, b = _Flaky(RateLimitError("429"), 99), _Flaky(RateLimitError("429"), 1)
    fe = translate_engines.FallbackEngine([a, b], ["claude", "deepseek"])
    assert fe.translate_batch(["z"], {}) == ["ok:z"]
    assert a.calls == 3 and b.calls == 2 and fe.active == 1


def test_backoff_is_capped(monkeypatch, _no_sleep):
    monkeypatch.setattr(translate_engines, "FALLBACK_TRANSIENT_RETRIES", 6)
    a = _Flaky(RateLimitError("429"), 6)
    fe = translate_engines.FallbackEngine([a], ["claude"])
    fe.translate_batch(["z"], {})
    assert max(_no_sleep) == translate_engines.FALLBACK_BACKOFF_CAP_SECONDS


# ---- B-06: failed-attempt spend counts ------------------------------------

def test_failed_attempt_with_reported_tokens_counts_against_that_engine(isolated_db, engines):
    a = engines["claude"](model="claude-sonnet-5")
    b = engines["deepseek"](model="deepseek-v4-flash")
    logged = []
    fe = translate_engines.FallbackEngine(
        [a, b], ["claude", "deepseek"],
        failed_usage_cb=lambda choice, eng, *tokens: logged.append((choice, tokens)))

    def billed_then_auth_error(z, c):
        a.last_usage = {"input_tokens": 1000000, "output_tokens": 0}  # a billed parse retry
        raise AuthError("401")
    a.translate_batch = billed_then_auth_error
    fe.translate_batch(["z"], {})
    assert fe.active == 1
    assert fe.spent[0] > 0 and fe.spent[1] > 0
    assert logged == [("claude", (1000000, 0, 0, 0))]


def test_failed_attempt_without_tokens_adds_nothing(isolated_db, engines):
    a, b = engines["claude"](model="m"), engines["deepseek"](model="m")
    logged = []
    fe = translate_engines.FallbackEngine([a, b], ["claude", "deepseek"],
                                          failed_usage_cb=lambda *x: logged.append(x))
    a.translate_batch = lambda z, c: (_ for _ in ()).throw(AuthError("401"))
    fe.translate_batch(["z"], {})
    assert fe.spent[0] == 0.0 and logged == []


def test_translate_run_logs_failed_attempt_usage(isolated_db, engines):
    engines["claude"].fail = RateLimitError("429")
    real = engines["claude"].translate_batch

    def billed_then_fail(self, z, c):
        self.last_usage = {"input_tokens": 1000, "output_tokens": 10}
        return real(self, z, c)
    engines["claude"].translate_batch = billed_then_fail
    did = _seed(1)
    job = _wait(svc.start_translate_run(did, engine_name="claude",
                                        fallback_chain=[{"engine": "deepseek"}])["job_id"])
    assert job["status"] == "done"
    summary = db.get_usage_summary(did)
    # 1 + retries billed claude attempts, plus deepseek's successful batch
    assert summary["input_tokens"] == (1 + translate_engines.FALLBACK_TRANSIENT_RETRIES) * 1000 \
        + 1000000


def test_failed_attempt_does_not_recount_previous_batch_usage(isolated_db, engines):
    """An engine that sets last_usage only on success (DeepL/Google) keeps
    the previous batch's usage when it fails; that must not count again."""
    a = engines["claude"](model="claude-sonnet-5")
    b = engines["deepseek"](model="deepseek-v4-flash")
    logged = []
    fe = translate_engines.FallbackEngine([a, b], ["claude", "deepseek"],
                                          failed_usage_cb=lambda *x: logged.append(x))
    fe.translate_batch(["z"], {})               # succeeds, last_usage stays set
    spent = fe.spent[0]
    a.translate_batch = lambda z, c: (_ for _ in ()).throw(AuthError("401"))
    fe.translate_batch(["z"], {})
    assert fe.spent[0] == spent and logged == []


def test_api_path_refuses_more_than_two_fallbacks(isolated_db, engines):
    """The API schema still allows 3 entries; the shared rule caps it at 2,
    the same limit as the CLI and React."""
    did = _seed(1)
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, engine_name="claude", fallback_chain=[
            {"engine": "deepseek"}, {"engine": "gemini"}, {"engine": "ollama"}])
    assert translate_engines.fallback_chain_error(["claude", "deepseek", "gemini"]) is None
