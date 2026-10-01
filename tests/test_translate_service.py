"""
Tests for services/translate_service.py: Migration Slice 11's read-only
"list engines" / "list history" half of the standalone translate tool
(tabs/translate_tab.py), and Migration Slice 13's translate() action.
Migration Slice 17's clear_history() is covered by TestClearHistory below.
"""

import pytest

import translate_engines
from services import settings_service, translate_service
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                      UnsupportedOperationError)


def _write_env(tmp_path, contents):
    env_path = tmp_path / ".env"
    env_path.write_text(contents, encoding="utf-8")
    return str(env_path)


def test_list_engines_includes_every_real_engine(tmp_path):
    env_path = _write_env(tmp_path, "")
    engines = translate_service.list_engines(env_path)
    names = {e["name"] for e in engines}
    assert names == set(translate_engines.ENGINES.keys())


def test_list_engines_reports_booleans(tmp_path):
    env_path = _write_env(tmp_path, "")
    engines = translate_service.list_engines(env_path)
    for e in engines:
        assert isinstance(e["free"], bool)
        assert isinstance(e["key_configured"], bool)
        assert e["models"] is None or isinstance(e["models"], list)


def test_list_engines_keyless_engines_report_configured(tmp_path):
    env_path = _write_env(tmp_path, "")
    engines = {e["name"]: e for e in translate_service.list_engines(env_path)}
    assert engines["test_offline"]["key_configured"] is True
    assert engines["nllb"]["key_configured"] is True
    assert engines["ollama"]["key_configured"] is True
    assert engines["libretranslate"]["key_configured"] is True


def test_list_engines_reflects_configured_key(tmp_path):
    env_path = _write_env(tmp_path, "BAIHE_CLAUDE_KEY=sk-should-not-leak\n")
    engines = {e["name"]: e for e in translate_service.list_engines(env_path)}
    assert engines["claude"]["key_configured"] is True
    assert engines["deepseek"]["key_configured"] is False


def test_list_engines_never_leaks_a_key_value(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    env_path = _write_env(tmp_path, "")
    engines = translate_service.list_engines(env_path)
    assert "sk-should-not-leak" not in repr(engines)


def test_list_engines_models_match_translate_engines_dicts(tmp_path):
    env_path = _write_env(tmp_path, "")
    engines = {e["name"]: e for e in translate_service.list_engines(env_path)}
    assert engines["claude"]["models"] == list(translate_engines.CLAUDE_MODELS.keys())
    assert engines["gemini"]["models"] == list(translate_engines.GEMINI_MODELS.keys())
    assert engines["ollama"]["models"] == list(translate_engines.OLLAMA_MODELS.keys())
    assert engines["nllb"]["models"] == list(translate_engines.NLLB_MODELS.keys())
    assert engines["test_offline"]["models"] is None


def test_list_engines_does_not_offer_the_removed_engines(tmp_path):
    env_path = _write_env(tmp_path, "BAIHE_DEEPL_KEY=stale\nBAIHE_GOOGLE_KEY=stale\n")
    names = {e["name"] for e in translate_service.list_engines(env_path)}
    assert not names & {"deepl", "google"}
    assert not set(translate_engines.ENGINES) & translate_engines.REMOVED_ENGINES
    assert "stale" not in repr(settings_service.key_status(env_path))
    assert not {"deepl", "google"} & set(settings_service.key_status(env_path))


def test_list_history_returns_what_was_saved(isolated_db):
    import db
    db.save_translate_history("zh", "en", "test_offline", "你好", "[TEST] Hello")
    history = translate_service.list_history()
    assert len(history) == 1
    assert history[0]["source_language"] == "zh"
    assert history[0]["target_language"] == "en"
    assert history[0]["engine"] == "test_offline"
    assert history[0]["source_text"] == "你好"
    assert history[0]["translated_text"] == "[TEST] Hello"


def test_list_history_respects_limit(isolated_db):
    import db
    for i in range(5):
        db.save_translate_history("zh", "en", "test_offline", f"src-{i}", f"out-{i}")
    history = translate_service.list_history(limit=2)
    assert len(history) == 2


class TestTranslate:
    def test_test_offline_produces_deterministic_output_and_saves_history(self, isolated_db):
        result = translate_service.translate("你好", "test_offline", "zh", "en")
        assert result == {"translated_text": "[TEST] 你好"}
        history = translate_service.list_history()
        assert len(history) == 1
        assert history[0]["engine"] == "test_offline"
        assert history[0]["translated_text"] == "[TEST] 你好"

    def test_unknown_engine_is_invalid_input(self, isolated_db):
        with pytest.raises(InvalidInputError):
            translate_service.translate("hi", "not_a_real_engine", "zh", "en")

    def test_unsupported_direction_is_refused(self, isolated_db):
        # libretranslate en->zh is explicitly refused by
        # standalone_direction_support -- see translate_engines.py.
        with pytest.raises(UnsupportedOperationError):
            translate_service.translate("hello", "libretranslate", "en", "zh")

    def test_libretranslate_uses_saved_url_and_sends_no_placeholder_key(
            self, isolated_db, monkeypatch):
        monkeypatch.setenv("BAIHE_LIBRETRANSLATE_URL", "http://lt.example:5001/")
        calls = []

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"translatedText": "Hello"}

        def fake_post(url, json=None, timeout=None):
            calls.append((url, json))
            return _Resp()

        import requests
        monkeypatch.setattr(requests, "post", fake_post)
        result = translate_service.translate("你好", "libretranslate", "zh", "en")
        assert result == {"translated_text": "Hello"}
        assert calls[0][0] == "http://lt.example:5001/translate"
        assert "api_key" not in calls[0][1]

    def test_missing_key_raises_dependency_unavailable(self, isolated_db, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("BAIHE_CLAUDE_KEY", raising=False)
        with pytest.raises(DependencyUnavailableError):
            translate_service.translate(
                "hi", "claude", "zh", "en", env_path=str(tmp_path / "no-such-.env"))

    def test_resolve_api_key_nllb_is_none_not_missing(self):
        # nllb has no ENV_NAMES entry at all -- resolve_key returns None for
        # it, and translate() must not treat that as a "missing key" the
        # way it would for claude/deepseek/etc. (checked in translate()'s
        # own `api_key is None and engine_name != "nllb"` guard).
        assert translate_service.resolve_api_key("nllb") is None

    def test_resolve_api_key_test_offline_is_literal_offline(self):
        assert translate_service.resolve_api_key("test_offline") == "offline"

    def test_resolve_api_key_ollama_defaults_to_local(self, tmp_path):
        env_path = _write_env(tmp_path, "")
        assert translate_service.resolve_api_key("ollama", env_path) == "local"


class TestClearHistory:
    def test_without_confirm_raises_and_leaves_history_untouched(self, isolated_db):
        import db
        db.save_translate_history("zh", "en", "test_offline", "你好", "[TEST] Hello")
        with pytest.raises(InvalidInputError):
            translate_service.clear_history()
        assert len(db.list_translate_history()) == 1

    def test_confirm_false_raises_and_leaves_history_untouched(self, isolated_db):
        import db
        db.save_translate_history("zh", "en", "test_offline", "你好", "[TEST] Hello")
        with pytest.raises(InvalidInputError):
            translate_service.clear_history(confirm=False)
        assert len(db.list_translate_history()) == 1

    def test_confirm_true_clears_history(self, isolated_db):
        import db
        db.save_translate_history("zh", "en", "test_offline", "你好", "[TEST] Hello")
        result = translate_service.clear_history(confirm=True)
        assert result == {"cleared": True}
        assert db.list_translate_history() == []

    def test_confirm_true_on_empty_history_is_not_an_error(self, isolated_db):
        import db
        assert db.list_translate_history() == []
        result = translate_service.clear_history(confirm=True)
        assert result == {"cleared": True}


def test_list_engines_gemini_label_reflects_free_tier_setting(isolated_db, tmp_path):
    from services import settings_service
    env_path = str(tmp_path / ".env")

    def label():
        return {e["name"]: e["label"]
                for e in translate_service.list_engines(env_path)}["gemini"]
    assert label() == translate_engines.ENGINE_NOTES["gemini"]
    settings_service.set_settings({"gemini_free_tier": True}, env_path)
    assert label() == translate_engines.GEMINI_FREE_TIER_NOTE
