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

    headers = {}

    def iter_content(self, size):
        yield json.dumps(self._body).encode()

    def close(self):
        pass

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
        assert item["replacement"] == "deepseek-flash"
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

        def fake_get(url, headers=None, timeout=None, allow_redirects=True, stream=False):
            seen.update(url=url, headers=headers, timeout=timeout, redirects=allow_redirects)
            return FakeResp({"data": [{"id": "claude-sonnet-5-5"}, {"id": "claude-haiku-4-5-20251001"}]})
        monkeypatch.setattr(requests, "get", fake_get)
        status = svc.check_providers()
        item = _item(status, "claude", "claude-opus-4-8")
        assert item["status"] == "not_listed"
        assert "claude-opus-4-8" in item["message"] and "claude" in item["message"]
        assert status["warnings"] >= 1
        # Key in a header, never the URL; a timeout on the call.
        assert "SECRETKEY" not in seen["url"] and seen["headers"]["x-api-key"].startswith("sk-ant-")
        assert seen["timeout"] and seen["redirects"] is False
        assert _item(status, "claude", "claude-sonnet-5-5")["status"] == "current"

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
        out = svc.switch_preset_model(pid, "deepseek-chat", "deepseek-flash")
        assert out["to_model"] == "deepseek-flash"
        p = db.list_presets()[0]
        assert (p["engine_model"], p["translation_engine"], p["style_preset"], p["locale"]) == \
            ("deepseek-flash", "deepseek", "novel", "en-GB")

    def test_stale_view_is_a_conflict(self, isolated_db):
        db.save_preset("P", translation_engine="deepseek", engine_model="deepseek-v4-pro")
        pid = db.list_presets()[0]["id"]
        with pytest.raises(ConflictError):
            svc.switch_preset_model(pid, "deepseek-chat", "deepseek-flash")

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
        db.save_preset("P", translation_engine="claude", engine_model="claude-opus-4-8")
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


class TestOfferProviderModels:
    def _cache(self, claude=None, gemini=None, deepseek=None, ok=True):
        engines = {}
        for name, ids in (("claude", claude), ("gemini", gemini), ("deepseek", deepseek)):
            if ids is not None:
                engines[name] = {"ok": ok, "models": ids}
        db.set_app_setting(svc.CHECK_CACHE_KEY, json.dumps({"checked_at": "2026-10-01T00:00:00",
                                                            "engines": engines}))

    def _models(self, name):
        return next(e for e in translate_service.list_engines() if e["name"] == name)

    def test_off_by_default_offers_only_built_in(self, isolated_db):
        self._cache(claude=["claude-sonnet-5", "claude-sonnet-6"])
        e = self._models("claude")
        assert e["models"] == list(translate_engines.CLAUDE_MODELS)
        assert e["model_labels"] == {}
        assert self._models("deepseek")["models"] is None

    def test_on_without_a_check_adds_nothing(self, isolated_db):
        db.set_app_setting("offer_provider_models", True)
        assert self._models("claude")["models"] == list(translate_engines.CLAUDE_MODELS)
        self._cache(claude=["claude-sonnet-6"], ok=False)
        assert self._models("claude")["models"] == list(translate_engines.CLAUDE_MODELS)

    def test_on_adds_listed_chat_models_with_label(self, isolated_db):
        db.set_app_setting("offer_provider_models", True)
        self._cache(claude=["claude-sonnet-5", "claude-sonnet-6", "claude-embed-1", "other-1",
                            "claude-Bad Id", "claude-sonnet-6"],
                    gemini=["gemini-flash-latest", "gemini-9-pro", "gemini-embedding-001",
                            "gemini-2.5-flash-image", "imagen-4"],
                    deepseek=["deepseek-flash", "deepseek-v5"])
        c = self._models("claude")
        assert c["models"] == list(translate_engines.CLAUDE_MODELS) + ["claude-sonnet-6"]
        assert "highest Claude rate" in c["model_labels"]["claude-sonnet-6"]
        assert self._models("gemini")["models"][-1:] == ["gemini-9-pro"]
        assert len(self._models("gemini")["models"]) == len(translate_engines.GEMINI_MODELS) + 1
        assert self._models("deepseek")["models"] == ["deepseek-flash", "deepseek-v5"]

    def test_openai_offers_only_listed_gpt5_and_later_chat_models(self, isolated_db):
        db.set_app_setting("offer_provider_models", True)
        listed = ["gpt-5-mini", "gpt-6-luna", "gpt-5.6-luna", "gpt-4", "gpt-4o", "o3",
                  "gpt-5-codex", "gpt-5-pro", "gpt-realtime-2", "gpt-5-mini-tts",
                  "gpt-image-2", "whisper-1", "text-embedding-4", "gpt-6-Bad Id"]
        db.set_app_setting(svc.CHECK_CACHE_KEY, json.dumps({
            "checked_at": "2026-10-01T00:00:00", "engines": {"openai": {"ok": True, "models": listed}}}))
        o = self._models("openai")
        assert o["models"] == list(translate_engines.OPENAI_MODELS) + ["gpt-6-luna", "gpt-5.6-luna"]
        assert "high ceiling" in o["model_labels"]["gpt-6-luna"]
        assert svc._override_error("openai", "gpt-6-luna") is None
        assert svc._override_error("openai", "gpt-4") is not None

    def test_never_calls_network(self, isolated_db, monkeypatch):
        import requests
        db.set_app_setting("offer_provider_models", True)
        self._cache(claude=["claude-sonnet-6"])
        monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("network"))
        assert "claude-sonnet-6" in self._models("claude")["models"]

    def test_extra_accepted_for_runs_and_presets_only_when_on(self, isolated_db):
        from services import translate_run_service as run
        self._cache(claude=["claude-sonnet-6"])
        with pytest.raises(InvalidInputError):
            run._require_offered_model("claude", "claude-sonnet-6")
        db.set_app_setting("offer_provider_models", True)
        run._require_offered_model("claude", "claude-sonnet-6")
        run._require_offered_model("claude", "claude-sonnet-5")
        with pytest.raises(InvalidInputError):
            run._require_offered_model("claude", "claude-sonnet-7")
        assert svc.get_status()["extra_models"] == {"claude": ["claude-sonnet-6"]}
        assert svc._offered("claude")[-1] == "claude-sonnet-6"


class TestUnpricedModelCost:
    def test_unpriced_models_use_highest_rate_of_their_provider(self):
        p = translate_engines.PRICING_PER_MILLION_TOKENS
        for prefix in ("claude-", "gemini-", "deepseek-"):
            fam = [r for m, r in p.items() if m.startswith(prefix)]
            expected = (max(r["input"] for r in fam) + max(r["output"] for r in fam))
            assert translate_engines.estimate_cost(prefix + "brand-new", 1_000_000, 1_000_000) == \
                pytest.approx(expected)
        assert translate_engines.estimate_cost("claude-brand-new", 1_000_000, 0) == 10.0
        assert translate_engines.estimate_cost("unrelated-model", 1000, 1000) == 0.0

    @pytest.mark.parametrize("model,rates", [
        ("claude-sonnet-9", (3.0, 15.0)),
        ("claude-opus-9", (5.0, 25.0)),
        ("claude-haiku-9", (1.0, 5.0)),
        ("claude-brand-new", (10.0, 50.0)),
        ("gemini-3.2-flash-lite", (0.30, 2.50)),
        ("gemini-4-flash", (0.75, 3.75)),
        ("gemini-4-pro", (2.0, 12.0)),
        ("gemini-brand-new", (2.0, 12.0)),
    ])
    def test_unpriced_model_uses_highest_rate_of_its_tier(self, model, rates):
        assert model not in translate_engines.PRICING_PER_MILLION_TOKENS
        assert translate_engines.estimate_cost(model, 1_000_000, 0) == pytest.approx(rates[0])
        assert translate_engines.estimate_cost(model, 0, 1_000_000) == pytest.approx(rates[1])


class _StubDeepSeek:
    name = "deepseek"

    def __init__(self, api_key, model="deepseek-flash"):
        self.model = model


class TestSavedClaudeModelsSurviveDefaultChange:
    def test_a_saved_older_id_is_kept_and_still_runs(self, isolated_db):
        from services import translate_run_service as run
        pid = db.save_preset("P", translation_engine="claude", engine_model="claude-opus-4-8")
        assert next(p for p in db.list_presets() if p["id"] == pid)["engine_model"] == "claude-opus-4-8"
        for old in ("claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6"):
            run._require_offered_model("claude", old)

    def test_an_unknown_id_is_still_refused(self, isolated_db):
        from services import translate_run_service as run
        with pytest.raises(InvalidInputError):
            run._require_offered_model("claude", "claude-opus-9-9")


class TestModelOverrides:
    """A user-chosen replacement for a built-in default or a tier's model."""

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        monkeypatch.setitem(translate_engines.ENGINES, "deepseek", _StubDeepSeek)

    def _cache(self, **engines):
        db.set_app_setting(svc.CHECK_CACHE_KEY, json.dumps({
            "checked_at": "2026-10-01T00:00:00",
            "engines": {n: {"ok": True, "models": ids} for n, ids in engines.items()}}))

    def _default_item(self, engine):
        return next(i for i in svc.get_status()["items"]
                    if i["kind"] == "default" and i["engine"] == engine)

    def test_no_override_means_built_in(self, isolated_db):
        assert translate_engines.effective_default_model("deepseek") == "deepseek-flash"
        assert translate_engines.get_engine("deepseek", "k").model == "deepseek-flash"
        item = self._default_item("deepseek")
        assert item["is_override"] is False and item["builtin_model"] == "deepseek-flash"

    def test_override_applies_in_get_engine_and_offered_and_runs(self, isolated_db):
        from services import translate_run_service as run
        out = svc.set_model_override("default", "deepseek", "deepseek-flash", "deepseek-v4-pro")
        assert out["to_model"] == "deepseek-v4-pro"
        assert translate_engines.get_engine("deepseek", "k").model == "deepseek-v4-pro"
        # an explicit model still wins
        assert translate_engines.get_engine("deepseek", "k", "deepseek-x1").model == "deepseek-x1"
        deepseek = next(e for e in translate_service.list_engines() if e["name"] == "deepseek")
        assert deepseek["models"] == ["deepseek-flash", "deepseek-v4-pro"]
        run._require_offered_model("deepseek", "deepseek-v4-pro")
        run._require_offered_model("deepseek", "deepseek-flash")
        with pytest.raises(InvalidInputError):
            run._require_offered_model("deepseek", "deepseek-other")
        assert run._default_model("deepseek") == "deepseek-v4-pro"
        item = self._default_item("deepseek")
        assert item["model"] == "deepseek-v4-pro" and item["is_override"] is True
        assert item["builtin_model"] == "deepseek-flash"

    def test_cost_of_an_unpriced_override_uses_the_highest_provider_rate(self, isolated_db):
        svc.set_model_override("default", "deepseek", "deepseek-flash", "deepseek-v9-new")
        assert "deepseek-v9-new" not in translate_engines.PRICING_PER_MILLION_TOKENS
        engine = translate_engines.get_engine("deepseek", "k")
        assert translate_engines.estimate_cost_for_engine(engine, 1_000_000, 1_000_000) > 0

    def test_tier_override_applies_to_tiers_and_offered(self, isolated_db):
        from services import translate_run_service as run
        tier = translate_engines.WORKFLOW_TIERS["standard"]
        svc.set_model_override("tier", "standard", tier["engine_model"], "claude-opus-4-8")
        assert translate_engines.effective_tier_model("standard") == "claude-opus-4-8"
        assert translate_engines.effective_tier("standard")["engine_model"] == "claude-opus-4-8"
        assert translate_engines.WORKFLOW_TIERS["standard"]["engine_model"] == tier["engine_model"]
        assert translate_engines.effective_tier("release")["engine_model"] == "claude-opus-5-5"
        # the tier model the form receives is the effective one
        db_id = db.create_drama(title_en="T", status="aligned")
        assert run.apply_workflow_tier(db_id, "standard")["engine_model"] == "claude-opus-4-8"
        item = next(i for i in svc.get_status()["items"] if i["kind"] == "tier" and i["key"] == "standard")
        assert item["is_override"] and item["model"] == "claude-opus-4-8"
        assert item["builtin_model"] == tier["engine_model"]

    def test_draft_tier_follows_the_engine_default_override(self, isolated_db):
        svc.set_model_override("default", "deepseek", "deepseek-flash", "deepseek-v4-pro")
        assert translate_engines.effective_tier("draft")["engine_model"] == "deepseek-v4-pro"
        assert translate_engines.effective_tier_model("draft") == "deepseek-v4-pro"
        with pytest.raises(InvalidInputError):
            svc.set_model_override("tier", "draft", "deepseek-v4-pro", "deepseek-v4-x")

    @pytest.mark.parametrize("engine,to_model", [
        ("deepseek", "gpt-4"), ("deepseek", "deepseek-embed-1"), ("deepseek", "deepseek-v4 pro"),
        ("deepseek", "deepseek-../x"), ("deepseek", ""), ("deepseek", None),
        ("claude", "claude-not-in-picker-9"), ("claude", "gpt-4"), ("ollama", "anything:7b"),
        ("libretranslate", "x-1"),
    ])
    def test_invalid_override_is_rejected_and_stores_nothing(self, isolated_db, engine, to_model):
        cur = translate_engines.effective_default_model(engine)
        with pytest.raises(InvalidInputError):
            svc.set_model_override("default", engine, cur, to_model)
        assert db.get_app_setting(translate_engines.MODEL_OVERRIDE_DEFAULTS_KEY) is None

    def test_picker_engine_takes_picker_or_provider_listed_models(self, isolated_db):
        self._cache(claude=["claude-sonnet-6", "claude-embed-1"])
        cur = translate_engines.effective_default_model("claude")
        picker_model = next(m for m in translate_engines.CLAUDE_MODELS if m != cur)
        svc.set_model_override("default", "claude", cur, picker_model)
        svc.set_model_override("default", "claude", picker_model, "claude-sonnet-6")
        assert "claude-sonnet-6" in next(
            e for e in translate_service.list_engines() if e["name"] == "claude")["models"]

    def test_unknown_kind_key_and_same_or_builtin_model(self, isolated_db):
        with pytest.raises(InvalidInputError):
            svc.set_model_override("tier", "nope", "a", "claude-sonnet-5")
        with pytest.raises(InvalidInputError):
            svc.set_model_override("default", "nope", "a", "claude-sonnet-5")
        with pytest.raises(InvalidInputError):
            svc.set_model_override("preset", "1", "a", "b")
        with pytest.raises(InvalidInputError):
            svc.set_model_override("default", "deepseek", "deepseek-flash", "deepseek-flash")

    def test_stale_from_model_is_a_conflict(self, isolated_db):
        svc.set_model_override("default", "deepseek", "deepseek-flash", "deepseek-v4-pro")
        with pytest.raises(ConflictError):
            svc.set_model_override("default", "deepseek", "deepseek-flash", "deepseek-v4-max")
        assert translate_engines.effective_default_model("deepseek") == "deepseek-v4-pro"

    def test_clear_restores_the_built_in(self, isolated_db):
        svc.set_model_override("default", "deepseek", "deepseek-flash", "deepseek-v4-pro")
        out = svc.clear_model_override("default", "deepseek")
        assert out["model"] == "deepseek-flash"
        assert translate_engines.get_engine("deepseek", "k").model == "deepseek-flash"
        assert svc.clear_model_override("default", "deepseek")["model"] == "deepseek-flash"
        assert next(e for e in translate_service.list_engines()
                    if e["name"] == "deepseek")["models"] is None

    def test_overrides_for_removed_engines_and_odd_values_are_ignored(self, isolated_db):
        db.set_app_setting(translate_engines.MODEL_OVERRIDE_DEFAULTS_KEY,
                           {"gone-engine": "x-1", "deepseek": "bad value ../", "claude": 5})
        db.set_app_setting(translate_engines.MODEL_OVERRIDE_TIERS_KEY, ["not", "a", "dict"])
        assert translate_engines.effective_default_model("deepseek") == "deepseek-flash"
        assert translate_engines.override_models("deepseek") == []
        assert translate_engines.effective_tier_model("standard") == \
            translate_engines.WORKFLOW_TIERS["standard"]["engine_model"]
        svc.get_status()

    def test_saved_presets_are_never_touched(self, isolated_db):
        db.save_preset("P", translation_engine="deepseek", engine_model="deepseek-flash")
        svc.set_model_override("default", "deepseek", "deepseek-flash", "deepseek-v4-pro")
        assert db.list_presets()[0]["engine_model"] == "deepseek-flash"

    def test_candidates_are_the_listed_models_and_replacement(self, isolated_db):
        item = self._default_item("deepseek")
        assert item["candidates"] == []
        self._cache(deepseek=["deepseek-flash", "deepseek-v4-pro", "deepseek-embed-1", "other-2"])
        item = self._default_item("deepseek")
        assert item["candidates"] == ["deepseek-v4-pro"]

    def test_cli_get_engine_uses_the_override(self, isolated_db):
        svc.set_model_override("default", "deepseek", "deepseek-flash", "deepseek-v4-pro")
        import cli
        seen = {}
        import argparse
        import contextlib
        import io
        from core import Line
        did = db.create_drama(title_en="T", status="aligned")
        db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])

        def fake_translate(lines, engine, **kwargs):
            seen["model"] = engine.model
            return lines, []
        import unittest.mock as mock
        with mock.patch.object(translate_engines, "translate_lines_with_engine", fake_translate):
            args = argparse.Namespace(id=did, status=None, engine="deepseek", api_key="k", model=None,
                                      style_note=None, style_preset="audio_drama", locale="en-US",
                                      force=False, ollama_num_ctx=None)
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_translate(args)
        assert seen["model"] == "deepseek-v4-pro"


def test_an_oversized_model_list_is_refused(monkeypatch):
    import requests

    class Big(FakeResp):
        headers = {"Content-Length": "9999999"}
    monkeypatch.setattr(requests, "get", lambda *a, **k: Big({}))
    with pytest.raises(ValueError, match="too large"):
        svc._fetch_models("claude", "sk-ant-key")


def test_a_model_list_that_outgrows_the_cap_while_streaming_is_refused(monkeypatch):
    import requests

    class Endless(FakeResp):
        def iter_content(self, size):
            while True:
                yield b"x" * size
    monkeypatch.setattr(svc, "MAX_RESPONSE_BYTES", 1000)
    monkeypatch.setattr(requests, "get", lambda *a, **k: Endless({}))
    with pytest.raises(ValueError, match="too large"):
        svc._fetch_models("claude", "sk-ant-key")
