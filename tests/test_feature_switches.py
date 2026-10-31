"""Feature switches (lib/features.py, ApiSettings.features_off): off removes the area's routes, on changes nothing."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings, load_settings
from api.server import create_app
from lib import features

ALL_ON = {"jellyfin": True, "sources": True, "extension": True}


def _client(off=""):
    settings = ApiSettings(auth_mode="off", features_off=features.parse(off))
    return TestClient(create_app(settings), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False)


def _paths(client):
    return set(client.get("/api/openapi.json").json()["paths"])


def test_everything_is_on_when_unset():
    assert load_settings({}).features_off == frozenset()
    assert features.status(frozenset()) == ALL_ON


@pytest.mark.parametrize("raw", ["", " , ", "nonsense", "JELLYFIN-ish", None])
def test_unknown_or_blank_values_leave_everything_on(raw):
    assert features.parse(raw) == frozenset()


def test_names_are_trimmed_and_case_insensitive():
    assert features.parse(" Jellyfin , SOURCES ") == {"jellyfin", "sources"}


def test_load_settings_reads_the_environment():
    assert load_settings({features.ENV_VAR: "extension"}).features_off == {"extension"}


def test_load_settings_reads_env_file_ahead_of_environment(monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "read_env_file", lambda *a: {features.ENV_VAR: "jellyfin"})
    monkeypatch.delenv(features.ENV_VAR, raising=False)
    assert load_settings().features_off == {"jellyfin"}


def test_default_app_mounts_every_area(isolated_db):
    client = _client()
    paths = _paths(client)
    assert any(p.startswith("/api/jellyfin") for p in paths)
    assert any(p.startswith("/api/sources") for p in paths)
    assert any(p.startswith("/api/extension") for p in paths)
    assert any(p.startswith("/api/ext-bridge") for p in paths)
    assert client.get("/api/meta").json()["features"] == ALL_ON


def test_off_unmounts_only_that_area(isolated_db):
    client = _client("jellyfin")
    paths = _paths(client)
    assert not any(p.startswith("/api/jellyfin") for p in paths)
    assert any(p.startswith("/api/sources") for p in paths)
    assert any(p.startswith("/api/extension") for p in paths)
    assert client.get("/api/jellyfin/status").status_code == 404
    assert client.get("/api/meta").json()["features"] == {"jellyfin": False, "sources": True,
                                                          "extension": True}


def test_all_off_still_serves_the_rest_of_the_app(isolated_db):
    client = _client("jellyfin,sources,extension")
    paths = _paths(client)
    for prefix in ("/api/jellyfin", "/api/source-domains", "/api/ext-bridge"):
        assert not any(p.startswith(prefix) for p in paths), prefix
    # Pasted-text import and Discover's pasted listing stay: other areas call them.
    assert {p for p in paths if p.startswith("/api/sources")} == {
        "/api/sources/url/preview-pasted", "/api/sources/url/import-pasted",
        "/api/sources/url/extractions", "/api/sources/url/identify-media",
        "/api/sources/url/identify-media/resource", "/api/sources/url/preflight"}
    assert "/api/discover/bulk-extract/pasted" in paths
    # Device tokens stay revocable with the extension off.
    assert {p for p in paths if p.startswith("/api/extension")} == {
        "/api/extension/devices", "/api/extension/devices/{device_token_id}/revoke"}
    assert "/api/auth/device-tokens" in paths
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/library/dramas").status_code == 200
    assert client.get("/api/meta").json()["features"] == {n: False for n in features.FEATURES}


def test_off_sources_does_not_start_the_chapter_scheduler(isolated_db, monkeypatch):
    from api import background
    from sources import chapter_check

    started = []
    monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda: started.append(1))
    monkeypatch.setattr(background, "_started", None)
    try:
        state = background.start_background_services(frozenset({"sources"}))
    finally:
        background._started = None
    assert state["chapter_scheduler"] is False
    assert started == []


@pytest.mark.parametrize("off,expected", [(frozenset(), 1), (frozenset({"extension"}), 0)])
def test_extension_endpoint_starts_only_while_the_switch_is_on(isolated_db, monkeypatch, off, expected):
    import page_server
    from api import background
    from sources import store

    started = []
    monkeypatch.setattr(store, "get_setting", lambda name: True)
    monkeypatch.setattr(page_server, "ensure_server_started", lambda: started.append(1))
    monkeypatch.setattr(background, "_started", None)
    try:
        background.start_background_services(off)
    finally:
        background._started = None
    assert len(started) == expected


def test_unknown_names_are_reported_at_start(isolated_db, caplog):
    settings = load_settings({features.ENV_VAR: "jellyfin, jellyfn"})
    assert settings.features_unknown == ("jellyfn",)
    with caplog.at_level("WARNING"):
        create_app(settings)
    assert "jellyfn" in caplog.text


def test_blank_env_file_line_overrides_the_environment(monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "read_env_file", lambda *a: {features.ENV_VAR: ""})
    monkeypatch.setenv(features.ENV_VAR, "jellyfin")
    assert load_settings().features_off == frozenset()
