"""
Tests for services/translate_service.py: Migration Slice 11's read-only
"list engines" / "list history" half of the standalone translate tool
(tabs/translate_tab.py), and Migration Slice 13's translate() action.
Migration Slice 17's clear_history() is covered by TestClearHistory below.
"""

import io

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
    assert engines["fake"]["key_configured"] is True
    assert engines["fake_mt"]["key_configured"] is True
    assert engines["ollama"]["key_configured"] is True


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
    assert engines["ollama"]["models"] == (list(translate_engines.OLLAMA_MODELS.keys())
                                           + list(translate_engines.OLLAMA_CLOUD_MODELS.keys()))
    assert engines["fake"]["models"] is None


def test_list_engines_does_not_offer_the_removed_engines(tmp_path):
    """Stale keys for the removed DeepL, Google and LibreTranslate engines can
    still sit in an old .env; they must neither revive the engine nor leak
    through the key status or settings overview."""
    env_path = _write_env(tmp_path, "BAIHE_DEEPL_KEY=stale\nBAIHE_GOOGLE_KEY=stale\n"
                          "BAIHE_LIBRETRANSLATE_URL=http://stale.example:5000\n")
    names = {e["name"] for e in translate_service.list_engines(env_path)}
    assert not names & {"deepl", "google", "libretranslate"}
    assert not set(translate_engines.ENGINES) & translate_engines.REMOVED_ENGINES
    assert "stale" not in repr(settings_service.key_status(env_path))
    assert not {"deepl", "google", "libretranslate", "libretranslate_url"} & set(
        settings_service.key_status(env_path))
    assert "stale.example" not in repr(settings_service.get_settings_overview(env_path))


def test_list_history_returns_what_was_saved(isolated_db):
    import db
    db.save_translate_history("zh", "en", "fake", "你好", "[TEST] Hello")
    history = translate_service.list_history()
    assert len(history) == 1
    assert history[0]["source_language"] == "zh"
    assert history[0]["target_language"] == "en"
    assert history[0]["engine"] == "fake"
    assert history[0]["source_text"] == "你好"
    assert history[0]["translated_text"] == "[TEST] Hello"


def test_list_history_respects_limit(isolated_db):
    import db
    for i in range(5):
        db.save_translate_history("zh", "en", "fake", f"src-{i}", f"out-{i}")
    history = translate_service.list_history(limit=2)
    assert len(history) == 2


class TestTranslate:
    def test_test_offline_produces_deterministic_output_and_saves_history(self, isolated_db):
        result = translate_service.translate("你好", "fake", "zh", "en")
        assert result == {"translated_text": "[TEST] 你好"}
        history = translate_service.list_history()
        assert len(history) == 1
        assert history[0]["engine"] == "fake"
        assert history[0]["translated_text"] == "[TEST] 你好"

    def test_unknown_engine_is_invalid_input(self, isolated_db):
        with pytest.raises(InvalidInputError):
            translate_service.translate("hi", "not_a_real_engine", "zh", "en")

    def test_unsupported_direction_is_refused(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_engines, "standalone_direction_support",
                            lambda *a: (False, "Not supported."))
        with pytest.raises(UnsupportedOperationError):
            translate_service.translate("hello", "fake_mt", "en", "zh")

    @pytest.mark.parametrize("engine", ["libretranslate", "nllb"])
    def test_a_removed_engine_is_refused_with_a_clear_message(self, isolated_db, engine):
        with pytest.raises(InvalidInputError, match=f"The {engine} engine was removed"):
            translate_service.translate("hello", engine, "zh", "en")

    def test_missing_key_raises_dependency_unavailable(self, isolated_db, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("BAIHE_CLAUDE_KEY", raising=False)
        with pytest.raises(DependencyUnavailableError):
            translate_service.translate(
                "hi", "claude", "zh", "en", env_path=str(tmp_path / "no-such-.env"))

    def test_a_keyless_engine_never_resolves_to_a_missing_key(self):
        # fake_mt has no ENV_NAMES entry, but a keyless engine resolves to the
        # literal "local" so translate()'s `api_key is None` guard never fires.
        assert translate_service.resolve_api_key("fake_mt") == "local"

    def test_resolve_api_key_ollama_defaults_to_local(self, tmp_path):
        env_path = _write_env(tmp_path, "")
        assert translate_service.resolve_api_key("ollama", env_path) == "local"


class TestClearHistory:
    def test_without_confirm_raises_and_leaves_history_untouched(self, isolated_db):
        import db
        db.save_translate_history("zh", "en", "fake", "你好", "[TEST] Hello")
        with pytest.raises(InvalidInputError):
            translate_service.clear_history()
        assert len(db.list_translate_history()) == 1

    def test_confirm_false_raises_and_leaves_history_untouched(self, isolated_db):
        import db
        db.save_translate_history("zh", "en", "fake", "你好", "[TEST] Hello")
        with pytest.raises(InvalidInputError):
            translate_service.clear_history(confirm=False)
        assert len(db.list_translate_history()) == 1

    def test_confirm_true_clears_history(self, isolated_db):
        import db
        db.save_translate_history("zh", "en", "fake", "你好", "[TEST] Hello")
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


class TestOllamaUnavailable:
    """An unreachable or model-less Ollama is a typed 503 with a plain
    sentence and a stable reason -- never the URL or requests' own text."""

    URL = "http://192.168.7.9:11434"

    def _fail_with(self, monkeypatch, exc=None, status=None):
        import requests

        def fake_post(url, json=None, timeout=None, stream=None):
            assert timeout
            if exc is not None:
                raise exc
            resp = requests.Response()
            resp.status_code = status
            resp.url = url
            resp.raw = io.BytesIO()
            return resp
        monkeypatch.setattr("requests.post", fake_post)

    def _call(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_service.settings_service, "resolve_key",
                            lambda name, *a, **k: self.URL if name == "ollama_url" else "local")
        return translate_service.translate("你好", "ollama", "zh", "en", model="qwen3:8b")

    @pytest.mark.parametrize("make, reason", [
        (lambda r: r.ConnectionError(f"HTTPConnectionPool(host='192.168.7.9', port=11434): refused {TestOllamaUnavailable.URL}"), "ollama_unreachable"),
        (lambda r: r.ConnectTimeout(f"timed out {TestOllamaUnavailable.URL}"), "ollama_unreachable"),
        (lambda r: r.ReadTimeout("read timed out"), "ollama_timeout"),
    ])
    def test_connection_failures_map_to_a_plain_error(self, isolated_db, monkeypatch, make, reason):
        import requests
        self._fail_with(monkeypatch, exc=make(requests))
        with pytest.raises(DependencyUnavailableError) as info:
            self._call(isolated_db, monkeypatch)
        assert info.value.details == {"reason": reason}
        assert "192.168" not in info.value.message and "11434" not in info.value.message
        assert "http" not in info.value.message.lower()

    def test_unreachable_message_is_the_plain_sentence(self, isolated_db, monkeypatch):
        import requests
        self._fail_with(monkeypatch, exc=requests.ConnectionError("refused"))
        with pytest.raises(DependencyUnavailableError, match="Ollama isn't running. Start it, or pick another translator in Settings."):
            self._call(isolated_db, monkeypatch)

    def test_a_model_that_is_not_pulled_names_the_model(self, isolated_db, monkeypatch):
        self._fail_with(monkeypatch, status=404)
        with pytest.raises(DependencyUnavailableError) as info:
            self._call(isolated_db, monkeypatch)
        assert info.value.details == {"reason": "ollama_model_missing"}
        assert "qwen3:8b" in info.value.message and "192.168" not in info.value.message

    def test_other_http_errors_are_left_alone(self, isolated_db, monkeypatch):
        import requests
        self._fail_with(monkeypatch, status=500)
        with pytest.raises(requests.HTTPError):
            self._call(isolated_db, monkeypatch)

    def test_the_api_body_has_no_url(self, isolated_db, monkeypatch):
        import requests
        from fastapi.testclient import TestClient
        from api.server import create_app
        from api.api_config import ApiSettings
        self._fail_with(monkeypatch, exc=requests.ConnectionError(self.URL))
        monkeypatch.setattr(translate_service.settings_service, "resolve_key",
                            lambda name, *a, **k: self.URL if name == "ollama_url" else "local")
        client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                            headers={"X-Baihe-Local": "1"})
        resp = client.post("/api/translate", json={
            "text": "你好", "engine": "ollama", "source_language": "zh", "target_language": "en"})
        assert resp.status_code == 503
        err = resp.json()["error"]
        assert err["code"] == "dependency_unavailable"
        assert err["details"] == {"reason": "ollama_unreachable"}
        assert "192.168" not in resp.text and "11434" not in resp.text
