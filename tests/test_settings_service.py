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

import sqlite3

import pytest

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
    assert set(overview) == {"engine_keys", "gpu_limit_enabled", "gpu_max_parallel", "unload_ollama_before_transcribe", "notify_on_completion",
                             "use_gpu", "gemini_free_tier", "bulk_auto_resume", "offer_provider_models", "remote_uploads",
                             "preferences", "endpoints",
                             "upload_max_mb_from_env", "effective_upload_max_mb",
                             "monthly_cap_env_usd", "effective_monthly_cap_usd", "month_spend_usd",
                             "month_spend_counted_usd", "month_spend_reset_at", "choices"}
    assert overview["engine_keys"]["claude"] is True
    assert isinstance(overview["gpu_limit_enabled"], bool)
    assert isinstance(overview["notify_on_completion"], bool)


def test_get_settings_overview_never_leaks_a_key_value(tmp_path, isolated_db):
    env_path = _write_env(tmp_path, "BAIHE_CLAUDE_KEY=sk-should-not-leak\n")
    overview = settings_service.get_settings_overview(env_path)
    assert "sk-should-not-leak" not in repr(overview)


def test_an_unreadable_setting_warns_once_per_key_and_returns_the_default(monkeypatch):
    import applog
    import db
    from lib import settings_schema

    def boom(_key):
        raise sqlite3.OperationalError("database is locked: /secret/path/library.db")

    warnings = []

    class _Log:
        def warning(self, msg, *args):
            warnings.append(msg % args)

    monkeypatch.setattr(db, "get_app_setting", boom)
    monkeypatch.setattr(applog, "get_logger", lambda: _Log())
    monkeypatch.setattr(settings_service, "_warned_unreadable_keys", set())
    expected = settings_schema.default_of(settings_schema.BY_KEY["developer_mode"])
    assert settings_service.get("developer_mode") == expected
    assert settings_service.get("developer_mode") == expected
    assert warnings == ["Setting 'developer_mode' could not be read; using its default."]


def test_include_prereleases_can_only_be_changed_in_developer_mode(isolated_db):
    import db
    from lib.errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        settings_service.set_settings({"update.include_prereleases": True})
    assert settings_service.get("update.include_prereleases") is False
    db.set_app_setting("assistant.developer_mode", True)
    settings_service.set_settings({"update.include_prereleases": True})
    assert settings_service.get("update.include_prereleases") is True


def test_overview_reports_which_ocr_backends_can_run_and_why_not(isolated_db, monkeypatch):
    import ocr
    from services import settings_service
    monkeypatch.setattr(ocr.importlib.util, "find_spec",
                        lambda name: object() if name == "pytesseract" else None)
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: "/usr/bin/tesseract")
    status = settings_service.preference_choices()["ocr_backend_status"]
    assert status["tesseract"] == {"installed": True, "reason": None, "package": None}
    assert status["manga_ocr"]["installed"] is False
    assert status["manga_ocr"]["package"] == "manga-ocr"
    assert "manga-ocr" in status["manga_ocr"]["reason"]
    assert status["paddle"]["package"] == "paddleocr"
    assert "auto" not in status


def test_remote_uploads_switch_needs_developer_mode_and_lapses(isolated_db):
    import db
    from lib.errors import InvalidInputError as Refused
    assert settings_service.remote_uploads_on() is False
    with pytest.raises(Refused):
        settings_service.set_settings({"remote_uploads": True})
    db.set_app_setting("assistant.developer_mode", True)
    overview = settings_service.set_settings({"remote_uploads": True})
    assert overview["remote_uploads"] is True
    on_at = db.get_app_setting("remote_uploads.on_at")
    days = settings_service.REMOTE_UPLOADS_DAYS * 86400
    assert settings_service.remote_uploads_on(on_at + days - 1) is True
    assert settings_service.remote_uploads_on(on_at + days) is False
    assert settings_service.remote_uploads_on(on_at - 1) is False
    db.set_app_setting("assistant.developer_mode", False)
    assert settings_service.remote_uploads_on(on_at) is False
    assert settings_service.get_settings_overview()["remote_uploads"] is False
    db.set_app_setting("assistant.developer_mode", True)
    settings_service.set_settings({"remote_uploads": False})
    assert settings_service.remote_uploads_on(on_at) is False
