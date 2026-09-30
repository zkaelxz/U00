"""
Step 40: model deprecation / migration assistant
(services/model_registry_service.py). Mocked provider responses only.
"""
import json

import pytest

import db
import translate_engines
from services import model_registry_service as svc
from services import translate_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     RateLimitedError)


class FakeResp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _reset_rate_limit(monkeypatch):
    monkeypatch.setattr(svc, "_last_check_started", 0.0)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: {"claude": "sk-ant-SECRETKEY1234567890abcdef"}.get(name))


def _item(status, engine, model):
    return next(i for i in status["items"] if i["engine"] == engine and i["model"] == model)


class TestRegistry:
    def test_registry_file_loads_and_is_well_formed(self):
        reg = svc.load_registry()
        assert reg, "the shipped registry should not be empty"
        for (engine, model), e in reg.items():
            assert engine in translate_engines.ENGINES
            assert e["status"] in svc.STATUSES

    def test_missing_registry_is_empty(self, tmp_path):
        assert svc.load_registry(str(tmp_path / "nope.json")) == {}

    def test_retired_preset_model_is_flagged_with_replacement(self, isolated_db):
        db.save_preset("Old DS", translation_engine="deepseek", engine_model="deepseek-chat")
        item = _item(svc.get_status(), "deepseek", "deepseek-chat")
        assert item["status"] == "retired" and item["kind"] == "preset"
        assert "deepseek-chat" in item["message"] and "deepseek" in item["message"]
        assert item["replacement"] == "deepseek-v4-flash"
        assert item["can_switch"] is True

    def test_defaults_and_tiers_are_listed_but_not_switchable(self, isolated_db):
        items = svc.get_status()["items"]
        kinds = {i["kind"] for i in items}
        assert {"default", "tier"} <= kinds
        assert not any(i["can_switch"] for i in items if i["kind"] != "preset")

    def test_status_never_calls_out(self, isolated_db, monkeypatch):
        monkeypatch.setattr(svc, "_fetch_models", lambda *a: pytest.fail("network call"))
        svc.get_status()


class TestProviderCheck:
    def test_model_no_longer_available_surfaces_clear_warning(self, isolated_db, keys, monkeypatch):
        import requests
        db.save_preset("P", translation_engine="claude", engine_model="claude-opus-4-8")
        seen = {}

        def fake_get(url, headers=None, timeout=None):
            seen.update(url=url, headers=headers, timeout=timeout)
            return FakeResp({"data": [{"id": "claude-sonnet-5"}, {"id": "claude-haiku-4-5-20251001"}]})
        monkeypatch.setattr(requests, "get", fake_get)
        status = svc.check_providers()
        item = _item(status, "claude", "claude-opus-4-8")
        assert item["status"] == "not_listed"
        assert "claude-opus-4-8" in item["message"] and "claude" in item["message"]
        assert status["warnings"] >= 1
        # Key in a header, never the URL; a timeout on the call.
        assert "SECRETKEY" not in seen["url"] and seen["headers"]["x-api-key"].startswith("sk-ant-")
        assert seen["timeout"]
        assert _item(status, "claude", "claude-sonnet-5")["status"] == "current"

    def test_engines_without_key_are_not_called(self, isolated_db, keys, monkeypatch):
        import requests
        calls = []
        monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append(url) or FakeResp({"data": []}))
        status = svc.check_providers()
        assert len(calls) == 1 and "anthropic" in calls[0]
        assert set(status["engines_checked"]) == {"claude"}

    def test_provider_error_is_redacted_and_cached(self, isolated_db, keys, monkeypatch):
        import requests

        def boom(url, **kw):
            raise RuntimeError("401 for key sk-ant-SECRETKEY1234567890abcdef")
        monkeypatch.setattr(requests, "get", boom)
        status = svc.check_providers()
        err = status["engines_checked"]["claude"]["error"]
        assert "SECRETKEY" not in err
        assert "SECRETKEY" not in db.get_app_setting(svc.CHECK_CACHE_KEY)
        # A failed check never marks a model as gone.
        assert all(i["status"] != "not_listed" for i in status["items"])

    def test_rate_limited(self, isolated_db, keys, monkeypatch):
        import requests
        monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResp({"data": []}))
        svc.check_providers(now=1000.0)
        with pytest.raises(RateLimitedError):
            svc.check_providers(now=1030.0)
        svc.check_providers(now=1061.0)

    def test_gemini_names_are_unprefixed(self):
        spec = svc._PROVIDER_LISTS["gemini"]
        assert spec["extract"]({"models": [{"name": "models/gemini-flash-latest"}]}) == ["gemini-flash-latest"]


class TestGuidedSwitch:
    def test_switch_only_changes_the_model(self, isolated_db):
        db.save_preset("Old DS", translation_engine="deepseek", engine_model="deepseek-chat",
                       style_preset="novel", locale="en-GB")
        pid = db.list_presets()[0]["id"]
        out = svc.switch_preset_model(pid, "deepseek-chat", "deepseek-v4-flash")
        assert out["to_model"] == "deepseek-v4-flash"
        p = db.list_presets()[0]
        assert (p["engine_model"], p["translation_engine"], p["style_preset"], p["locale"]) == \
            ("deepseek-v4-flash", "deepseek", "novel", "en-GB")

    def test_stale_view_is_a_conflict(self, isolated_db):
        db.save_preset("P", translation_engine="deepseek", engine_model="deepseek-v4-pro")
        pid = db.list_presets()[0]["id"]
        with pytest.raises(ConflictError):
            svc.switch_preset_model(pid, "deepseek-chat", "deepseek-v4-flash")

    def test_unoffered_model_rejected(self, isolated_db):
        db.save_preset("P", translation_engine="deepseek", engine_model="deepseek-chat")
        pid = db.list_presets()[0]["id"]
        with pytest.raises(InvalidInputError):
            svc.switch_preset_model(pid, "deepseek-chat", "gpt-9")

    def test_unknown_preset(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.switch_preset_model(999, "a", "b")

    def test_nothing_switches_without_the_call(self, isolated_db, keys, monkeypatch):
        import requests
        db.save_preset("Old DS", translation_engine="deepseek", engine_model="deepseek-chat")
        monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResp({"data": []}))
        svc.get_status()
        svc.check_providers()
        assert db.list_presets()[0]["engine_model"] == "deepseek-chat"
