"""
Tests for services/translate_service.py -- Migration Slice 11's read-only
"list engines" / "list history" half of the standalone translate tool
(tabs/translate_tab.py). Actually translating and clearing history are
deferred to a later slice and aren't covered here.
"""

import translate_engines
from services import translate_service


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
    assert engines["deepl"]["models"] is None
    assert engines["google"]["models"] is None
    assert engines["test_offline"]["models"] is None


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
