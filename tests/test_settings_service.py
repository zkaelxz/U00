"""
Tests for services/settings_service.py -- Migration Slice 10's read-only
Settings overview, shared by the FastAPI /api/settings route and (later)
the Streamlit Settings tab, which now imports ENV_NAMES from here instead
of keeping its own copy.

resolve_key() is server-side-only (D2: keys never leave the process over
HTTP) -- these tests confirm key_status()/get_settings_overview() report
presence only, never a value, and that ENV_NAMES resolution matches the
documented priority order and BOM-safe parsing.
"""

from services import settings_service


def _write_env(tmp_path, contents):
    env_path = tmp_path / ".env"
    env_path.write_text(contents, encoding="utf-8")
    return str(env_path)


def test_resolve_key_reads_env_file(tmp_path):
    env_path = _write_env(tmp_path, "BAIHE_CLAUDE_KEY=sk-test-123\n")
    assert settings_service.resolve_key("claude", env_path) == "sk-test-123"


def test_resolve_key_priority_order_prefers_first_env_name(tmp_path):
    env_path = _write_env(
        tmp_path, "ANTHROPIC_API_KEY=fallback\nBAIHE_CLAUDE_KEY=preferred\n")
    assert settings_service.resolve_key("claude", env_path) == "preferred"


def test_resolve_key_falls_back_to_second_env_name(tmp_path):
    env_path = _write_env(tmp_path, "ANTHROPIC_API_KEY=fallback-only\n")
    assert settings_service.resolve_key("claude", env_path) == "fallback-only"


def test_resolve_key_handles_bom(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_bytes("BAIHE_HF_TOKEN=hf-test\n".encode("utf-8-sig"))
    assert settings_service.resolve_key("hf_token", str(env_path)) == "hf-test"


def test_resolve_key_missing_file_returns_none(tmp_path):
    env_path = str(tmp_path / "does-not-exist.env")
    assert settings_service.resolve_key("claude", env_path) is None


def test_resolve_key_falls_back_to_os_environ(tmp_path, monkeypatch):
    env_path = str(tmp_path / "does-not-exist.env")
    monkeypatch.setenv("GROQ_API_KEY", "env-var-value")
    assert settings_service.resolve_key("groq", env_path) == "env-var-value"


def test_key_status_reports_booleans_not_values(tmp_path):
    env_path = _write_env(tmp_path, "BAIHE_CLAUDE_KEY=sk-should-not-leak\n")
    status = settings_service.key_status(env_path)
    assert status["claude"] is True
    assert status["deepseek"] is False
    assert "monthly_cap_usd" not in status
    for value in status.values():
        assert isinstance(value, bool)


def test_key_status_never_contains_a_configured_value(tmp_path):
    env_path = _write_env(tmp_path, "BAIHE_CLAUDE_KEY=sk-should-not-leak\n")
    status = settings_service.key_status(env_path)
    assert "sk-should-not-leak" not in repr(status)


def test_get_settings_overview_shape(tmp_path, isolated_db):
    env_path = _write_env(tmp_path, "BAIHE_CLAUDE_KEY=sk-test\n")
    overview = settings_service.get_settings_overview(env_path)
    assert set(overview) == {"engine_keys", "gpu_limit_enabled", "gpu_max_parallel", "notify_on_completion",
                             "use_gpu", "gemini_free_tier", "bulk_auto_resume", "offer_provider_models", "preferences", "endpoints",
                             "monthly_cap_env_usd", "effective_monthly_cap_usd", "month_spend_usd",
                             "month_spend_counted_usd", "month_spend_reset_at", "choices"}
    assert overview["engine_keys"]["claude"] is True
    assert isinstance(overview["gpu_limit_enabled"], bool)
    assert isinstance(overview["notify_on_completion"], bool)


def test_get_settings_overview_never_leaks_a_key_value(tmp_path, isolated_db):
    env_path = _write_env(tmp_path, "BAIHE_CLAUDE_KEY=sk-should-not-leak\n")
    overview = settings_service.get_settings_overview(env_path)
    assert "sk-should-not-leak" not in repr(overview)
