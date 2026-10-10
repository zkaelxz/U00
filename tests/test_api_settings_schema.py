"""GET /api/settings/schema: declared-settings metadata, never a value or a secret."""
import json

import pytest
from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from lib import settings_schema
from services import settings_service


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _rows(client):
    resp = client.get("/api/settings/schema")
    assert resp.status_code == 200
    return resp.json()["settings"]


def test_schema_has_no_secret_row_and_no_env_key(client):
    text = client.get("/api/settings/schema").text
    keys = {row["key"] for row in json.loads(text)["settings"]}
    secret_keys = {s.key for s in settings_schema.SETTINGS if s.secret or s.store != "db"}
    assert secret_keys and not keys & secret_keys
    # monthly_cap_usd is a stored preference that .env can also set; its row is
    # wanted, but the variable name must not appear.
    for env_key, env_names in settings_service.ENV_NAMES.items():
        if env_key != "monthly_cap_usd":
            assert env_key not in keys
        assert not any(name in text for name in env_names)


def test_schema_choices_are_resolved_and_values_absent(client):
    rows = {r["key"]: r for r in _rows(client)}
    engines = rows["ocr_backend"]["choices"]
    assert {"value": "auto", "label": "auto"} in engines
    assert rows["max_upload_mb"]["choices"] is None
    assert rows["default_locale"]["section"] == "Translation style"


def test_dev_only_rows_follow_developer_mode(client):
    assert not [r for r in _rows(client) if r["dev_only"]]
    db.set_app_setting("assistant.developer_mode", True)
    assert any(r["key"] == "assistant.model" for r in _rows(client))
