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

        def fake_get(url, headers=None, timeout=None, allow_redirects=True):
            seen.update(url=url, headers=headers, timeout=timeout, redirects=allow_redirects)
            return FakeResp({"data": [{"id": "claude-sonnet-5"}, {"id": "claude-haiku-4-5-20251001"}]})
        monkeypatch.setattr(requests, "get", fake_get)
        status = svc.check_providers()
        item = _item(status, "claude", "claude-opus-4-8")
        assert item["status"] == "not_listed"
        assert "claude-opus-4-8" in item["message"] and "claude" in item["message"]
        assert status["warnings"] >= 1
        # Key in a header, never the URL; a timeout on the call.
        assert "SECRETKEY" not in seen["url"] and seen["headers"]["x-api-key"].startswith("sk-ant-")
        assert seen["timeout"] and seen["redirects"] is False
        assert _item(status, "claude", "claude-sonnet-5")["status"] == "current"

    def test_engines_without_key_are_not_called(self, isolated_db, keys, monkeypatch):
        import requests
        calls = []
        monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append(url)
                            or FakeResp({"data": [{"id": "claude-sonnet-5"}]}))
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

    @pytest.mark.parametrize("body", [{"data": []}, {"unexpected": 1},
                                      {"data": [{"id": "claude-sonnet-5"}], "has_more": True}])
    def test_empty_or_partial_list_marks_nothing_gone(self, isolated_db, keys, monkeypatch, body):
        import requests
        db.save_preset("P", translation_engine="claude", engine_model="claude-opus-4-8")
        monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResp(body))
        status = svc.check_providers()
        assert status["engines_checked"]["claude"]["ok"] is False
        assert all(i["status"] != "not_listed" for i in status["items"])

    def test_offered_latest_alias_missing_is_not_flagged(self, isolated_db, monkeypatch):
        import requests
        monkeypatch.setattr(translate_service, "resolve_api_key",
                            lambda name, env_path=None: "g-key-123456789" if name == "gemini" else None)
        monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResp(
            {"models": [{"name": "models/gemini-3.1-flash-lite"}]}))
        status = svc.check_providers()
        default = next(i for i in status["items"] if i["engine"] == "gemini" and i["kind"] == "default")
        assert default["status"] == "unknown"

    def test_check_already_running_is_429(self, isolated_db, keys):
        svc._check_lock.acquire()
        try:
            with pytest.raises(RateLimitedError):
                svc.check_providers()
        finally:
            svc._check_lock.release()

    def test_rate_limited(self, isolated_db, keys, monkeypatch):
        import requests
        monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResp({"data": [{"id": "x"}]}))
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
        monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResp({"data": [{"id": "x"}]}))
        svc.get_status()
        svc.check_providers()
        assert db.list_presets()[0]["engine_model"] == "deepseek-chat"


class TestReviewFixes:
    def test_every_replacement_is_offered(self):
        for (engine, _m), e in svc.load_registry().items():
            if e.get("replacement"):
                assert e["replacement"] in svc._offered(engine), e

    def test_unoffered_preset_model_is_flagged_and_switchable(self, isolated_db):
        db.save_preset("P", translation_engine="gemini", engine_model="gemini-2.5-pro")
        item = _item(svc.get_status(), "gemini", "gemini-2.5-pro")
        assert item["status"] == "not_offered" and item["severity"] == 2
        assert item["can_switch"] and item["replacement"] in svc._offered("gemini")

    def test_extension_model_is_listed(self, isolated_db):
        from services import extension_service
        db.set_app_setting(extension_service.ENGINE_SETTING,
                           {"engine": "deepseek", "model": "deepseek-chat"})
        item = next(i for i in svc.get_status()["items"] if i["kind"] == "extension")
        assert item["status"] == "retired" and item["can_switch"] is False

    def test_switch_is_conditional_in_the_database(self, isolated_db):
        db.save_preset("P", translation_engine="deepseek", engine_model="deepseek-chat")
        pid = db.list_presets()[0]["id"]
        assert db.set_preset_engine_model(pid, "x", expected_model="other") is False
        assert db.list_presets()[0]["engine_model"] == "deepseek-chat"


def test_alias_listed_as_dated_snapshot_counts_as_listed(isolated_db, keys, monkeypatch):
    import requests
    db.save_preset("P", translation_engine="claude", engine_model="claude-sonnet-5")
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResp(
        {"data": [{"id": "claude-sonnet-5-20260101"}]}))
    status = svc.check_providers()
    item = next(i for i in status["items"] if i["model"] == "claude-sonnet-5" and i["kind"] == "preset")
    assert item["status"] == "current"


class TestClaudeAliases:
    """Anthropic's model list may return only dated snapshots; an undated or
    "-latest" alias the app uses must not read as "no longer listed" just
    because of that."""

    def _app_claude_models(self):
        models = set(translate_engines.CLAUDE_MODELS)
        models |= {t["engine_model"] for t in translate_engines.WORKFLOW_TIERS.values()
                   if t["translation_engine"] == "claude" and t.get("engine_model")}
        models.add(svc._default_model("claude"))
        return models

    def _check(self, monkeypatch, ids):
        import requests
        monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResp(
            {"data": [{"id": i} for i in ids]}))
        return svc.check_providers()

    def test_every_app_alias_matches_its_dated_snapshot(self, isolated_db, keys, monkeypatch):
        ids = [m if svc._DATED_SUFFIX.search(m) else m + "-20260101"
               for m in self._app_claude_models()]
        status = self._check(monkeypatch, ids)
        claude = [i for i in status["items"] if i["engine"] == "claude"]
        assert claude and all(i["status"] == "current" for i in claude), claude
        assert all(i["listed_by_provider"] is True for i in claude)

    def test_dated_only_list_without_a_match_is_cant_confirm(self, isolated_db, keys, monkeypatch):
        status = self._check(monkeypatch, ["claude-other-9-20260101"])
        claude = [i for i in status["items"] if i["engine"] == "claude"]
        for i in claude:
            if svc._DATED_SUFFIX.search(i["model"]):
                # A dated id is either listed or gone.
                assert i["status"] == "not_listed", i
            else:
                assert i["status"] == "unknown", i
                assert "can't be confirmed" in i["message"]
                assert i["listed_by_provider"] is None

    def test_undated_ids_in_the_list_make_absence_meaningful(self, isolated_db, keys, monkeypatch):
        status = self._check(monkeypatch, ["claude-sonnet-5", "claude-other-9-20260101"])
        item = next(i for i in status["items"]
                    if i["engine"] == "claude" and i["model"] == "claude-opus-4-8")
        assert item["status"] == "not_listed"

    @pytest.mark.parametrize("model,ids,expected", [
        ("claude-sonnet-5", ["claude-sonnet-5"], True),
        ("claude-sonnet-5", ["claude-sonnet-5-20260101"], True),
        ("claude-3-5-sonnet-latest", ["claude-3-5-sonnet-20241022"], True),
        ("claude-3-5-sonnet-latest", ["claude-3-5-sonnet"], True),
        ("claude-3-5-sonnet-latest", ["claude-sonnet-5"], None),
        # A longer model name sharing the prefix is not a snapshot of it.
        ("claude-sonnet-4", ["claude-sonnet-4-6-20260101"], None),
        ("claude-sonnet-4", ["claude-sonnet-4-6"], False),
        ("claude-haiku-4-5-20251001", ["claude-sonnet-5-20260101"], False),
        # Other engines: only "-latest" counts as an alias.
        ("gemini-flash-latest", ["gemini-3.1-flash-lite"], None),
        ("gemini-3.1-flash-lite", ["gemini-3.1-flash-lite-preview"], False),
        ("gemini-3.1-flash-lite", ["gemini-3.1-flash-lite-001"], True),
    ])
    def test_listed(self, model, ids, expected):
        engine = model.split("-", 1)[0]
        assert svc._listed(engine, model, ids) is expected
