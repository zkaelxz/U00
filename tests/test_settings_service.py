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
import time

import pytest

from services import settings_service
import stored_settings
from lib import settings_schema


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
                             "desktop_popups", "use_gpu", "gemini_free_tier", "bulk_auto_resume", "offer_provider_models", "remote_uploads", "remote_uploads_since", "remote_uploads_auto_off",
                             "preferences", "endpoints",
                             "upload_max_mb_from_env", "effective_upload_max_mb",
                             "monthly_cap_env_usd", "effective_monthly_cap_usd", "month_spend_usd",
                             "month_spend_imported_usd", "month_spend_counted_usd", "month_spend_reset_at", "choices"}
    assert overview["engine_keys"]["claude"] is True
    assert isinstance(overview["gpu_limit_enabled"], bool)
    assert isinstance(overview["notify_on_completion"], bool)



def test_desktop_popups_says_whether_a_pop_up_can_show(tmp_path, isolated_db, monkeypatch):
    from lib import desktop_session
    env_path = _write_env(tmp_path, "")
    monkeypatch.setattr(desktop_session, "has_desktop", lambda: False)
    assert settings_service.get_settings_overview(env_path)["desktop_popups"] is False
    monkeypatch.setattr(desktop_session, "has_desktop", lambda: True)
    assert settings_service.get_settings_overview(env_path)["desktop_popups"] is True

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
    monkeypatch.setattr(stored_settings, "_warned_unreadable_keys", set())
    expected = settings_schema.default_of(settings_schema.BY_KEY["developer_mode"])
    assert settings_service.get("developer_mode") == expected
    assert settings_service.get("developer_mode") == expected
    assert warnings == ["Setting 'developer_mode' could not be read; using its default."]


def test_include_prereleases_is_not_a_general_settings_write(isolated_db):
    from lib.errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        settings_service.set_settings({"update.include_prereleases": True})


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


def test_remote_uploads_switch_stays_on_until_turned_off(isolated_db):
    import db
    assert settings_service.remote_uploads_on() is False
    assert not settings_service.get("developer_mode")
    overview = settings_service.set_settings({"remote_uploads": True})
    assert overview["remote_uploads"] is True
    assert overview["remote_uploads_auto_off"] is False
    on_at = db.get_app_setting("remote_uploads.since")
    assert overview["remote_uploads_since"] == on_at
    year = 365 * 86400
    assert settings_service.remote_uploads_on(on_at + year) is True
    assert settings_service.remote_uploads_on(on_at - 1) is False
    settings_service.set_settings({"remote_uploads": False})
    assert settings_service.remote_uploads_on(on_at) is False
    assert settings_service.get_settings_overview()["remote_uploads_since"] is None


def test_remote_uploads_auto_off_is_opt_in_and_lapses_after_seven_days(isolated_db):
    import db
    settings_service.set_settings({"remote_uploads": True})
    settings_service.set_settings({"remote_uploads_auto_off": True})
    start = max(db.get_app_setting("remote_uploads.since"), db.get_app_setting("remote_uploads.auto_off_from"))
    days = settings_service.REMOTE_UPLOADS_DAYS * 86400
    assert settings_service.remote_uploads_on(start + days - 1) is True
    assert settings_service.remote_uploads_on(start + days) is False
    settings_service.set_settings({"remote_uploads_auto_off": False})
    assert settings_service.remote_uploads_on(start + days * 10) is True


def test_a_switch_from_before_auto_off_existed_keeps_its_seven_day_lapse(isolated_db):
    import db
    on_at = time.time() - 8 * 86400
    db.set_app_setting("remote_uploads", True)
    db.set_app_setting("remote_uploads.since", on_at)
    assert db.get_app_setting("remote_uploads_auto_off") is None
    assert settings_service.remote_uploads_on() is False
    assert settings_service.remote_uploads_on(on_at + 86400) is True
    settings_service.set_settings({"remote_uploads": True})
    assert settings_service.remote_uploads_on(time.time() + 30 * 86400) is True


def test_a_remote_uploads_stamp_from_the_developer_mode_days_does_not_count(isolated_db):
    import db
    db.set_app_setting("remote_uploads", True)
    db.set_app_setting("remote_uploads.on_at", time.time())
    assert settings_service.remote_uploads_on() is False


def test_stored_settings_refuses_a_choices_setting_it_cannot_check(isolated_db):
    # The modules below services/ read through stored_settings, which can't
    # build a choices list; a key with one must go through settings_service.get.
    with pytest.raises(ValueError):
        stored_settings.read("default_engine")
    assert settings_service.get("default_engine") == stored_settings.read(
        "default_engine", lambda: settings_service._choices_for("default_engine"))


def test_a_failing_choices_list_still_reads_as_the_default(isolated_db, monkeypatch):
    monkeypatch.setattr(settings_service, "_choices_for", lambda key: 1 / 0)
    monkeypatch.setattr(stored_settings, "_warned_unreadable_keys", set())
    expected = settings_schema.default_of(settings_schema.BY_KEY["default_engine"])
    assert settings_service.get("default_engine") == expected


@pytest.mark.parametrize("choice", [True, False])
def test_choosing_auto_off_does_not_bring_a_lapsed_switch_back(isolated_db, choice):
    import db
    db.set_app_setting("remote_uploads", True)
    db.set_app_setting("remote_uploads.since", time.time() - 30 * 86400)
    settings_service.set_settings({"remote_uploads_auto_off": choice})
    assert settings_service.remote_uploads_on() is False
    assert settings_service.remote_uploads_on(time.time() + 365 * 86400) is False
