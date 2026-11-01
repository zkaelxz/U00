"""
Tests for services/translate_service.py: Migration Slice 11's read-only
"list engines" / "list history" half of the standalone translate tool
(tabs/translate_tab.py), and Migration Slice 13's translate() action.
Migration Slice 17's clear_history() is covered by TestClearHistory below.
"""

import io

import pytest

from engine_backends import engine_registry, pricing
from engine_backends import local as engine_local
from engine_backends import standalone as engine_standalone
from services import settings_service, translate_service
from lib.errors import (DependencyUnavailableError, InvalidInputError,
                                      UnsupportedOperationError)


def _write_env(tmp_path, contents):
    env_path = tmp_path / ".env"
    env_path.write_text(contents, encoding="utf-8")
    return str(env_path)


def test_list_engines_includes_every_real_engine(tmp_path):
    env_path = _write_env(tmp_path, "")
    engines = translate_service.list_engines(env_path)
    names = {e["name"] for e in engines}
    assert names == set(engine_registry.ENGINES.keys())


def test_list_engines_reports_booleans(tmp_path):
    env_path = _write_env(tmp_path, "")
    engines = translate_service.list_engines(env_path)
    for e in engines:
        assert isinstance(e["free"], bool)
        assert isinstance(e["key_configured"], bool)
        assert e["models"] is None or isinstance(e["models"], list)


def test_list_engines_keyless_engines_report_configured(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path, "")
    monkeypatch.setattr(engine_local, "check_ollama_reachable", lambda *a, **k: True)
    engines = {e["name"]: e for e in translate_service.list_engines(env_path, probe_ollama=True)}
    assert engines["fake"]["key_configured"] is True
    assert engines["fake_mt"]["key_configured"] is True
    assert engines["ollama"]["key_configured"] is True


def test_list_engines_ollama_that_does_not_answer_is_not_usable(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path, "")
    monkeypatch.setattr(engine_local, "check_ollama_reachable", lambda *a, **k: False)
    engines = {e["name"]: e for e in translate_service.list_engines(env_path, probe_ollama=True)}
    assert engines["ollama"]["key_configured"] is False
    # A keyless engine does not depend on a package: only its server.
    assert engines["ollama"]["package_installed"] is True


def test_list_engines_offers_the_models_pulled_on_the_ollama_server(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path, "")
    monkeypatch.setattr(engine_local, "check_ollama_reachable", lambda *a, **k: True)
    monkeypatch.setattr(engine_local, "pulled_ollama_models",
                        lambda base: ["qwen3:4b", engine_local.OLLAMA_DEFAULT_MODEL])
    models = next(e for e in translate_service.list_engines(env_path, probe_ollama=True)
                  if e["name"] == "ollama")["models"]
    assert "qwen3:4b" in models
    assert models.count(engine_local.OLLAMA_DEFAULT_MODEL) == 1
    # Not offered when the server reads as down, and not asked without the probe.
    monkeypatch.setattr(engine_local, "check_ollama_reachable", lambda *a, **k: False)
    models = next(e for e in translate_service.list_engines(env_path, probe_ollama=True)
                  if e["name"] == "ollama")["models"]
    assert "qwen3:4b" not in models
    monkeypatch.setattr(engine_local, "pulled_ollama_models",
                        lambda base: (_ for _ in ()).throw(AssertionError("asked")))
    models = next(e for e in translate_service.list_engines(env_path)
                  if e["name"] == "ollama")["models"]
    assert "qwen3:4b" not in models


def test_the_probed_engine_list_asks_ollama_once(tmp_path, monkeypatch):
    import json
    from engine_backends import ollama_server as local
    calls = []

    class Resp:
        status = 200
        body = json.dumps({"models": [{"name": "qwen3:4b"}]}).encode()

    monkeypatch.setattr(local, "_pulled_models_cache", {})
    monkeypatch.setattr(local, "_ollama_reachability_cache", {})
    monkeypatch.setattr(local.http, "get", lambda url, **kw: calls.append(url) or Resp())
    engines = {e["name"]: e for e in translate_service.list_engines(_write_env(tmp_path, ""),
                                                                    probe_ollama=True)}
    assert engines["ollama"]["key_configured"] is True and "qwen3:4b" in engines["ollama"]["models"]
    assert calls == ["http://localhost:11434/api/tags"]


def test_pulled_ollama_models_reads_tags_caches_and_drops_unsafe_names(monkeypatch):
    import json
    from engine_backends import ollama_server as local
    calls = []

    class Resp:
        status = 200
        body = json.dumps({"models": [
            {"name": "qwen3:4b"}, {"model": "qwen3:4b"}, {"name": "../etc"}, {"name": "has space"},
            "junk", {"name": "nomic-embed-text:latest", "details": {"family": "nomic-bert"}},
            {"name": "bge-m3:latest", "details": {"family": "bert", "families": ["bert"]}},
            {"name": "mxbai-embed-large:latest"}]}).encode()

    monkeypatch.setattr(local, "_pulled_models_cache", {})
    monkeypatch.setattr(local, "_ollama_reachability_cache", {})
    monkeypatch.setattr(local.http, "get", lambda url, **kw: calls.append(url) or Resp())
    assert local.pulled_ollama_models("localhost:11434") == ["qwen3:4b"]
    assert local.pulled_ollama_models("http://localhost:11434/") == ["qwen3:4b"]
    assert calls == ["http://localhost:11434/api/tags"]


def test_pulled_ollama_models_is_empty_when_the_server_does_not_answer(monkeypatch):
    from engine_backends import ollama_server as local
    monkeypatch.setattr(local, "_pulled_models_cache", {})
    monkeypatch.setattr(local, "_ollama_reachability_cache", {})
    monkeypatch.setattr(local.http, "get", lambda url, **kw: (_ for _ in ()).throw(OSError("refused")))
    assert local.pulled_ollama_models("http://127.0.0.1:11434") == []
    assert local.check_ollama_reachable("http://127.0.0.1:11434") is False


def test_list_engines_makes_no_network_call_by_default(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(engine_local, "check_ollama_reachable", lambda *a, **k: seen.append(1) or False)
    engines = {e["name"]: e for e in translate_service.list_engines(_write_env(tmp_path, ""))}
    assert seen == [] and engines["ollama"]["key_configured"] is True


def test_list_engines_engine_whose_sdk_is_missing_is_not_usable_even_with_a_key(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path, "BAIHE_DEEPSEEK_KEY=k\nBAIHE_OPENAI_KEY=k\nBAIHE_CLAUDE_KEY=k\n")
    monkeypatch.setattr(translate_service.diagnostics, "check_dependency", lambda name: name != "openai")
    engines = {e["name"]: e for e in translate_service.list_engines(env_path)}
    for name in ("deepseek", "openai"):
        assert engines[name]["package_installed"] is False and engines[name]["key_configured"] is False
    assert engines["claude"]["package_installed"] is True and engines["claude"]["key_configured"] is True


def test_list_engines_an_installed_sdk_leaves_the_key_decision_alone(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path, "")
    monkeypatch.setattr(translate_service.diagnostics, "check_dependency", lambda name: True)
    engines = {e["name"]: e for e in translate_service.list_engines(env_path)}
    assert engines["deepseek"]["package_installed"] is True and engines["deepseek"]["key_configured"] is False


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
    assert engines["claude"]["models"] == list(pricing.CLAUDE_MODELS.keys())
    assert engines["gemini"]["models"] == list(pricing.GEMINI_MODELS.keys())
    assert engines["ollama"]["models"] == (list(engine_local.OLLAMA_MODELS.keys())
                                           + list(engine_local.OLLAMA_CLOUD_MODELS.keys()))
    assert engines["fake"]["models"] is None


def test_list_engines_does_not_offer_the_removed_engines(tmp_path):
    """Stale keys for the removed DeepL, Google and LibreTranslate engines can
    still sit in an old .env; they must neither revive the engine nor leak
    through the key status or settings overview."""
    env_path = _write_env(tmp_path, "BAIHE_DEEPL_KEY=stale\nBAIHE_GOOGLE_KEY=stale\n"
                          "BAIHE_LIBRETRANSLATE_URL=http://stale.example:5000\n")
    names = {e["name"] for e in translate_service.list_engines(env_path)}
    assert not names & {"deepl", "google", "libretranslate"}
    assert not set(engine_registry.ENGINES) & engine_registry.REMOVED_ENGINES
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
        monkeypatch.setattr(engine_standalone, "standalone_direction_support",
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

class _BilledEngine:
    name = "claude"
    model = "claude-sonnet-5-5"

    def __init__(self):
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, texts, context=None):
        self.last_usage = {"input_tokens": 1000, "output_tokens": 500}
        return [f"EN:{t}" for t in texts]


class TestTranslateSpending:
    def _paid_engine(self, monkeypatch, engine):
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **kw: engine)

    def _usage_rows(self):
        import contextlib
        import db
        with contextlib.closing(db.get_conn()) as conn:
            return [tuple(r) for r in conn.execute(
                "SELECT engine, operation, input_tokens, output_tokens, estimated_cost_usd "
                "FROM usage_log").fetchall()]

    def test_a_paid_translation_is_logged_with_its_real_cost(self, isolated_db, monkeypatch):
        self._paid_engine(monkeypatch, _BilledEngine())
        translate_service.translate("你好", "claude", "zh", "en")
        [(engine, operation, inp, out, cost)] = self._usage_rows()
        assert (engine, operation, inp, out) == ("claude", "translate_text", 1000, 500)
        assert cost == pytest.approx(pricing.estimate_cost("claude-sonnet-5-5", 1000, 500))
        assert cost > 0

    def test_usage_sums_every_chunk_group(self, isolated_db, monkeypatch):
        self._paid_engine(monkeypatch, _BilledEngine())
        # > 8 chunks, so standalone_translate makes a second translate_batch call
        text = "\n\n".join("段" * 1400 for _ in range(10))
        translate_service.translate(text, "claude", "zh", "en")
        # One row per request, so the monthly cap sees each as it is billed.
        rows = self._usage_rows()
        assert [(r[2], r[3]) for r in rows] == [(1000, 500), (1000, 500)]

    def test_cap_spent_refuses_a_paid_engine_before_any_call(self, isolated_db, monkeypatch):
        import db
        from services import translate_run_service
        engine = _BilledEngine()
        self._paid_engine(monkeypatch, engine)
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
        monkeypatch.setattr(db, "get_month_spend", lambda *a, **kw: 1.0)
        with pytest.raises(UnsupportedOperationError):
            translate_service.translate("你好", "claude", "zh", "en")
        assert engine.last_usage["input_tokens"] == 0 and self._usage_rows() == []

    def test_a_free_engine_ignores_the_cap(self, isolated_db, monkeypatch):
        import db
        from services import translate_run_service
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
        monkeypatch.setattr(db, "get_month_spend", lambda *a, **kw: 1.0)
        assert translate_service.translate("你好", "fake", "zh", "en") == {"translated_text": "[TEST] 你好"}
        assert all(row[4] == 0 for row in self._usage_rows())


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
    assert label() == engine_registry.ENGINE_NOTES["gemini"]
    settings_service.set_settings({"gemini_free_tier": True}, env_path)
    assert label() == engine_registry.GEMINI_FREE_TIER_NOTE


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


class TestPasteKeepsEveryLine:
    def test_history_saves_all_lines_and_the_message_counts_missing_ones(self, isolated_db, monkeypatch):
        import db

        class _Drops:
            name = "fake"
            free_tier = True

            def translate_batch(self, texts, context):
                return ["" if t == "2. b" else f"OUT:{t}" for t in texts]

        monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a, **k: "k")
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: _Drops())
        result = translate_service.translate("1. a\n2. b\n3. c", "fake", "zh", "en")
        assert result["translated_text"] == "OUT:1. a\n[untranslated] 2. b\nOUT:3. c"
        assert result["message"] == "1 line could not be translated and is marked [untranslated]."
        assert db.list_translate_history()[0]["translated_text"] == result["translated_text"]

    def test_estimate_counts_the_line_by_line_requests_the_run_makes(self, isolated_db):
        text = "\n".join(f"line {i}" for i in range(1, 21))
        result = translate_service.estimate(text, "fake", "zh", "en")
        assert result["requests"] == len(engine_standalone.standalone_requests(text, 8)) == 3
