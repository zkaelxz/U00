"""
Tests for the FastAPI foundation (api/): startup, health/meta, the
Library endpoints' contract, the shared error shape, and the CORS /
configuration rules. Uses FastAPI's TestClient against an
`isolated_db` library -- no server process, no network.

FastAPI is an optional dependency (requirements-optional.txt), so this
whole file skips cleanly on a core-only install, same as any other
optional-package test here.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient's transport

from fastapi.testclient import TestClient

from api.api_config import ApiSettings, load_settings
from api.server import create_app


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert {"code", "message"} <= set(body["error"])
    return body["error"]


class TestStartupAndSystem:
    def test_create_app_touches_no_database(self, isolated_db, monkeypatch):
        def boom():
            raise AssertionError("create_app opened the database")
        monkeypatch.setattr(isolated_db, "get_conn", boom)
        create_app(ApiSettings())

    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_meta(self, client):
        body = client.get("/api/meta").json()
        assert body["app"] == "Baihe Studio"
        assert body["api_version"] == "0.1"
        assert body["environment"] == "production"

    def test_openapi_documents_the_library_contract(self, client):
        spec = client.get("/api/openapi.json").json()
        assert "/api/library/dramas" in spec["paths"]
        assert "/api/library/dramas/{drama_id}" in spec["paths"]
        assert "DramaSummary" in spec["components"]["schemas"]
        assert client.get("/api/docs").status_code == 200


class TestLibraryEndpoints:
    def test_list_returns_contract_shape(self, client, isolated_db):
        did = isolated_db.create_drama(title_zh="白河", title_en="Baihe",
                                       custom_tags="Favorite, bl", audio_filename="source.mp3")
        body = client.get("/api/library/dramas").json()
        assert body["count"] == 1
        item = body["items"][0]
        assert item["id"] == did
        assert item["title_zh"] == "白河"
        assert item["custom_tags"] == ["Favorite", "bl"]
        # Internals stay out of the contract.
        assert "audio_filename" not in item
        assert "last_translate_errors" not in item

    def test_list_filters_match_the_service(self, client, isolated_db):
        isolated_db.create_drama(title_en="Keep", custom_tags="favorite, bl", status="aligned")
        isolated_db.create_drama(title_en="Drop", custom_tags="bl", status="aligned")
        resp = client.get("/api/library/dramas",
                          params={"quick_filter": "Favorite", "tag": ["bl"], "status": "aligned"})
        assert [d["title_en"] for d in resp.json()["items"]] == ["Keep"]

    def test_list_empty_library(self, client):
        assert client.get("/api/library/dramas").json() == {"items": [], "count": 0}

    def test_detail_reduces_filenames_to_flags(self, client, isolated_db):
        did = isolated_db.create_drama(title_en="X", audio_filename="source.mp3",
                                       summary="s")
        body = client.get(f"/api/library/dramas/{did}").json()
        assert body["summary"] == "s"
        assert body["has_audio"] is True
        assert body["has_cover_art"] is False
        assert not any(k.endswith("_filename") for k in body)

    def test_unknown_drama_is_404(self, client):
        resp = client.get("/api/library/dramas/999")
        assert resp.status_code == 404
        assert _error(resp)["code"] == "not_found"

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5"])
    def test_bad_drama_id_is_422(self, client, bad):
        resp = client.get(f"/api/library/dramas/{bad}")
        assert resp.status_code == 422
        assert _error(resp)["code"] == "validation_error"

    def test_bad_quick_filter_is_422_with_allowed_values(self, client):
        resp = client.get("/api/library/dramas", params={"quick_filter": "nope"})
        assert resp.status_code == 422
        err = _error(resp)
        assert err["code"] == "validation_error"
        assert "Favorite" in err["details"]["allowed"]

    def test_overlong_search_is_422(self, client):
        resp = client.get("/api/library/dramas", params={"search": "x" * 500})
        assert resp.status_code == 422


class TestErrorShape:
    def test_unknown_route_uses_error_shape(self, client):
        resp = client.get("/api/does-not-exist")
        assert resp.status_code == 404
        assert _error(resp)["code"] == "not_found"

    def test_wrong_method_is_unsupported_operation(self, client):
        resp = client.delete("/api/library/dramas")
        assert resp.status_code == 405
        assert _error(resp)["code"] == "unsupported_operation"

    def test_validation_error_never_echoes_the_input(self, client):
        secret = "sk-ant-api03-" + "A" * 40
        resp = client.get(f"/api/library/dramas/{secret}")
        assert resp.status_code == 422
        assert secret not in resp.text

    def test_unexpected_error_is_generic_500_without_internals(self, client, monkeypatch):
        from services import library_service

        def explode(**_):
            raise RuntimeError("/home/someone/library/library.db is locked; key=sk-ant-XYZ")
        monkeypatch.setattr(library_service, "list_library_dramas", explode)
        resp = client.get("/api/library/dramas")
        assert resp.status_code == 500
        err = _error(resp)
        assert err["code"] == "internal_error"
        assert "library.db" not in resp.text and "sk-ant" not in resp.text
        assert "Traceback" not in resp.text

    @pytest.mark.parametrize("exc_name, status, code", [
        ("UnsupportedOperationError", 400, "unsupported_operation"),
        ("DependencyUnavailableError", 503, "dependency_unavailable"),
        ("ServiceError", 500, "application_error"),
    ])
    def test_service_errors_map_to_status_and_code(self, client, monkeypatch,
                                                   exc_name, status, code):
        from services import library_service, service_errors
        exc_cls = getattr(service_errors, exc_name)

        def fail(**_):
            raise exc_cls("Needs something.")
        monkeypatch.setattr(library_service, "list_library_dramas", fail)
        resp = client.get("/api/library/dramas")
        assert resp.status_code == status
        assert _error(resp) == {"code": code, "message": "Needs something."}

    def test_service_error_message_is_redacted(self, client, monkeypatch):
        from services import library_service
        from services.service_errors import ServiceError
        key = "sk-ant-api03-" + "B" * 40

        def fail(**_):
            raise ServiceError(f"provider said no for {key}")
        monkeypatch.setattr(library_service, "list_library_dramas", fail)
        resp = client.get("/api/library/dramas")
        assert key not in resp.text


class TestConfigAndCors:
    def test_defaults_are_loopback_production_no_cors(self):
        s = load_settings({})
        assert (s.host, s.port, s.environment, s.cors_origins) == (
            "127.0.0.1", 8600, "production", ())

    def test_development_gets_the_vite_origins(self):
        s = load_settings({"BAIHE_API_ENV": "development"})
        assert "http://localhost:5173" in s.cors_origins

    def test_wildcard_origin_is_refused(self):
        with pytest.raises(ValueError):
            load_settings({"BAIHE_API_ENV": "development", "BAIHE_API_CORS_ORIGINS": "*"})

    @pytest.mark.parametrize("env", [{"BAIHE_API_PORT": "abc"}, {"BAIHE_API_PORT": "70000"},
                                     {"BAIHE_API_ENV": "staging"}])
    def test_bad_settings_fail_loudly(self, env):
        with pytest.raises(ValueError):
            load_settings(env)

    def test_production_sends_no_cors_headers(self, client):
        resp = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in resp.headers

    def test_development_allows_only_listed_origins(self, isolated_db):
        dev = TestClient(create_app(load_settings({"BAIHE_API_ENV": "development"})))
        ok = dev.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
        evil = dev.get("/api/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in evil.headers
