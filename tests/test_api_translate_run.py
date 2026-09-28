"""Tests for the Translate-stage config/estimate endpoints (Migration Slice 39)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import settings_service

BASE = "/api/translate-run/dramas"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _seed(**fields):
    did = db.create_drama(title_zh="D", **fields)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en=""),
                        Line(idx=1, start=1, end=2, zh="再见", en="bye")])
    return did


def test_config_audio_and_novel(client):
    a = client.get(f"{BASE}/{_seed()}/config")
    assert a.status_code == 200
    body = a.json()
    assert body["defaults"] == {"context_window": 6, "context_window_ahead": 3, "batch_size": 20}
    assert body["line_count"] == 2 and body["untranslated_count"] == 1
    assert body["engines"] and body["workflow_tiers"] and body["style_presets"]
    assert isinstance(body["cap_applies_by_engine"]["claude"], bool)
    n = client.get(f"{BASE}/{_seed(content_mode='novel_narration')}/config").json()
    assert n["defaults"] == {"context_window": 10, "context_window_ahead": 6, "batch_size": 30}
    assert n["default_style_preset"] == "novel"


def test_unknown_drama_404(client):
    for path in ("config", "estimate"):
        r = client.get(f"{BASE}/9999/{path}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


def test_estimate_free_engine(client):
    r = client.get(f"{BASE}/{_seed()}/estimate", params={"engine": "test_offline"})
    assert r.status_code == 200
    body = r.json()
    assert body["free"] is True and body["engine"] == "test_offline"
    assert body["target_line_count"] == 1


def test_estimate_validation(client):
    did = _seed()
    r = client.get(f"{BASE}/{did}/estimate", params={"engine": "nope"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    r = client.get(f"{BASE}/{did}/estimate", params={"engine": "deepl", "reflect": "true"})
    assert r.status_code == 400
    assert "error" in r.json()
    r = client.get(f"{BASE}/{did}/estimate", params={"job_cost_cap_usd": -1})
    assert r.status_code == 422


def test_no_secrets_in_responses(client, monkeypatch):
    fake = "sk-FAKE-SECRET-1234567890"
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **kw: fake)
    did = _seed()
    for path in ("config", "estimate"):
        assert fake not in client.get(f"{BASE}/{did}/{path}").text
