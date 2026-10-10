"""An omitted gemini_free_tier / free_tier falls back to the saved Settings
value on every server-side run path; an explicit false stays false. Also:
POST /api/translate never fetches a client-supplied Ollama URL, and
fix-flagged gets the same default cap as a translate run. Fully mocked."""
import pytest
from tests.saved_settings import patch_setting

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import (line_ai_service, review_jobs_service, settings_service,
                      translate_run_service, translate_service)
from services.service_errors import UnsupportedOperationError

PRO = next(iter(translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS))


class FakeEngine:
    model = "fake-model"
    supports_reference = True
    last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, texts, ctx=None):
        return [f"EN:{t}" for t in texts]


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    seen = []
    patch_setting(monkeypatch, "gemini_free_tier", True)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")

    def fake_get_engine(name, key, model=None, **kw):
        seen.append(kw)
        return FakeEngine()
    monkeypatch.setattr(translate_engines, "get_engine", fake_get_engine)
    monkeypatch.setattr(background_jobs, "start_job", lambda *a, **kw: seen.append(kw) or True)
    yield seen
    background_jobs.clear_all_jobs()


def _seed(flag=None):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好" * 20, en="hi", flag=flag)])
    return did


def test_resolve_helper(monkeypatch):
    assert settings_service.resolve_gemini_free_tier(None) is True
    assert settings_service.resolve_gemini_free_tier(False) is False
    patch_setting(monkeypatch, "gemini_free_tier", False)
    assert settings_service.resolve_gemini_free_tier(None) is False
    assert settings_service.resolve_gemini_free_tier(True) is True


def test_estimate_and_run_agree(_env):
    did = _seed()
    est = translate_run_service.estimate_translate_cost(did, "gemini")
    assert est["free"] is True and est["cap_applies"] is False
    with pytest.raises(UnsupportedOperationError, match="free tier"):
        translate_run_service.estimate_translate_cost(did, "gemini", model=PRO)
    with pytest.raises(UnsupportedOperationError, match="free tier"):
        translate_run_service.start_translate_run(did, "gemini", model=PRO)
    paid = translate_run_service.estimate_translate_cost(did, "gemini", gemini_free_tier=False)
    assert paid["free"] is False and paid["cap_applies"] is True


def test_explicit_false_run_is_not_blocked():
    did = _seed()
    try:
        translate_run_service.start_translate_run(did, "gemini", model=PRO,
                                                  gemini_free_tier=False)
    except UnsupportedOperationError as e:
        assert "free tier" not in str(e)


@pytest.mark.parametrize("start", [
    review_jobs_service.start_consistency_check,
    review_jobs_service.start_emotion_tagging,
    review_jobs_service.start_translation_notes,
    review_jobs_service.start_flag_review,
])
def test_review_jobs_default_to_setting(_env, start):
    did = _seed()
    start(did, engine_name="gemini")
    assert _env[0]["free_tier"] is True
    background_jobs.clear_all_jobs()
    _env.clear()
    start(did, engine_name="gemini", gemini_free_tier=False)
    assert _env[0]["free_tier"] is False
    with pytest.raises(UnsupportedOperationError):
        start(did, engine_name="gemini", model=PRO)


def test_fix_flagged_default_setting_and_cap(_env, monkeypatch):
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 7.0)
    monkeypatch.setattr(db, "get_month_spend", lambda *a, **kw: 0.0)
    did = _seed(flag="uncertain")
    review_jobs_service.start_fix_flagged(did, engine_name="gemini")
    assert _env[0]["free_tier"] is True and _env[1]["cost_cap_usd"] is None
    _env.clear()
    review_jobs_service.start_fix_flagged(did, engine_name="gemini", gemini_free_tier=False)
    # Same default as a translate run: no per-job cap, the monthly cap applies.
    assert _env[1]["cost_cap_usd"] == 7.0


def test_bulk_estimate_refused_like_run():
    did = _seed()
    with pytest.raises(UnsupportedOperationError, match="Gemini \\(paid\\)"):
        translate_run_service.estimate_translate_cost(did, "gemini", bulk=True)
    with pytest.raises(UnsupportedOperationError, match="Gemini \\(paid\\)"):
        translate_run_service.start_translate_run(did, "gemini", bulk=True)
    assert translate_run_service.estimate_translate_cost(
        did, "gemini", bulk=True, gemini_free_tier=False)["cap_applies"] is True


def test_config_follows_saved_setting(monkeypatch):
    did = _seed()
    cfg = translate_run_service.get_translate_config(did)
    assert "gemini" not in cfg["bulk_supported_engines"]
    assert cfg["cap_applies_by_engine"]["gemini"] is False
    assert cfg["cap_applies_by_engine"]["claude"] is True
    patch_setting(monkeypatch, "gemini_free_tier", False)
    cfg = translate_run_service.get_translate_config(did)
    assert "gemini" in cfg["bulk_supported_engines"]
    assert cfg["cap_applies_by_engine"]["gemini"] is True


def test_line_ai_defaults_to_setting(_env, monkeypatch):
    did = _seed()
    lid = db.load_lines(did)[0]["id"]
    with pytest.raises(UnsupportedOperationError):
        line_ai_service.explain_line(did, lid, "gemini", PRO)
    monkeypatch.setattr(line_ai_service, "run", lambda fn: "x")
    line_ai_service.explain_line(did, lid, "gemini")
    line_ai_service.improve_line(did, lid, "gemini", gemini_free_tier=False)
    assert [kw["free_tier"] for kw in _env] == [True, False]


def test_review_route_omitted_uses_setting(_env):
    did = _seed()
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = c.post(f"/api/review-jobs/dramas/{did}/consistency", json={"engine": "gemini"})
    assert r.status_code == 200, r.text
    assert _env[0]["free_tier"] is True


def test_api_translate_setting_and_no_client_url(_env, monkeypatch):
    real = settings_service.resolve_key
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda k, *a, **kw: "http://127.0.0.1:11434" if k == "ollama_url"
                        else real(k, *a, **kw))
    monkeypatch.setattr(translate_engines, "standalone_translate", lambda *a, **kw: "out")
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    body = {"text": "你好", "engine": "gemini", "source_language": "zh", "target_language": "en"}
    assert c.post("/api/translate", json=body).status_code == 200
    assert c.post("/api/translate", json={**body, "free_tier": False}).status_code == 200
    assert [kw["free_tier"] for kw in _env] == [True, False]
    _env.clear()
    r = c.post("/api/translate", json={**body, "engine": "ollama",
                                       "base_url": "http://169.254.169.254/latest"})
    assert r.status_code == 200, r.text
    assert _env[0]["base_url"] == "http://127.0.0.1:11434"
