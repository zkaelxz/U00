"""The keys-file settings list: set or not set per name, never a value."""
import json

from lib.env_settings import ENV_SETTINGS
from services import env_settings_service

SECRET = "sk-test-secret-value-123"


def _row(rows, name):
    return next(r for r in rows if r["name"] == name)


def test_a_key_in_the_keys_file_reads_as_set_and_the_value_is_not_returned(tmp_path, monkeypatch):
    monkeypatch.delenv("BAIHE_DISCORD_WEBHOOK_URL", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"BAIHE_DISCORD_WEBHOOK_URL={SECRET}\nBAIHE_NTFY_TOPIC_URL=\n")
    rows = env_settings_service.get_status(str(env))["settings"]
    assert _row(rows, "BAIHE_DISCORD_WEBHOOK_URL")["set"] is True
    assert _row(rows, "BAIHE_NTFY_TOPIC_URL")["set"] is False
    assert SECRET not in json.dumps(rows)


def test_a_line_in_the_keys_file_does_not_count_for_a_name_read_from_the_environment_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("BAIHE_API_PORT", raising=False)
    env = tmp_path / ".env"
    env.write_text("BAIHE_API_PORT=8601\n")
    assert _row(env_settings_service.get_status(str(env))["settings"], "BAIHE_API_PORT")["set"] is False
    monkeypatch.setenv("BAIHE_API_PORT", "8601")
    assert _row(env_settings_service.get_status(str(env))["settings"], "BAIHE_API_PORT")["set"] is True


def test_a_missing_keys_file_reads_as_nothing_set(tmp_path, monkeypatch):
    for s in ENV_SETTINGS:
        monkeypatch.delenv(s.name, raising=False)
    rows = env_settings_service.get_status(str(tmp_path / "none.env"))["settings"]
    assert len(rows) == len(ENV_SETTINGS) and not any(r["set"] for r in rows)


def test_the_route_returns_the_list_without_a_value(client, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"BAIHE_MEMBER_KEY_SECRET={SECRET}\n")
    monkeypatch.delenv("BAIHE_MEMBER_KEY_SECRET", raising=False)
    monkeypatch.setattr("services.settings_service.default_env_path", lambda: str(env))
    resp = client.get("/api/settings/keys-file")
    assert resp.status_code == 200
    assert _row(resp.json()["settings"], "BAIHE_MEMBER_KEY_SECRET")["set"] is True
    assert SECRET not in resp.text


def test_every_listed_name_is_still_read_by_the_program():
    import subprocess
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for s in ENV_SETTINGS:
        hit = subprocess.run(["git", "grep", "-q", "-F", s.name, "--", "*.py", ":!tests", ":!lib/env_settings.py"],
                             cwd=root).returncode
        assert hit == 0, f"{s.name} is no longer used anywhere: drop it from lib/env_settings.py"
