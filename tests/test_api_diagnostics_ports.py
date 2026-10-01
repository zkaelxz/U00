"""GET /api/diagnostics/ports: the ports panel's data (PC only, read-only).
No network, no real listeners."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import page_server
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, settings_service

PUBLIC = "https://baihe.example.com"


def _local(settings):
    return TestClient(create_app(settings), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False)


def _by_key(body):
    return {p["key"]: p for p in body["ports"]}


@pytest.fixture(autouse=True)
def _bridge_off(monkeypatch):
    monkeypatch.setattr(page_server, "server_running", lambda: False)


def test_service_defaults(isolated_db):
    ports = _by_key(settings_service.baihe_ports(8600))
    assert list(ports) == ["api", "household", "extension", "https"]
    assert ports["api"]["port"] == 8600 and ports["api"]["active"] is True
    assert ports["household"]["port"] is None and ports["household"]["active"] is False
    assert ports["extension"]["port"] == page_server.DEFAULT_PORT == 8756
    assert ports["extension"]["active"] is False
    assert ports["https"]["port"] == 443 and ports["https"]["active"] is False
    for p in ports.values():
        assert p["label"] and p["how_to_change"]


def test_service_reports_active_listeners(isolated_db, monkeypatch):
    monkeypatch.setattr(page_server, "server_running", lambda: True)
    ports = _by_key(settings_service.baihe_ports(8611, 8610, PUBLIC))
    assert ports["api"]["port"] == 8611
    assert ports["household"] == {**ports["household"], "port": 8610, "active": True}
    assert ports["extension"]["active"] is True
    assert ports["https"]["active"] is True
    # Household port alone, without a public address, is not remote access.
    assert _by_key(settings_service.baihe_ports(8600, 8610, ""))["https"]["active"] is False


def test_service_tolerates_a_bad_household_value(isolated_db):
    assert _by_key(settings_service.baihe_ports(8600, "x"))["household"]["port"] is None


def test_every_listed_port_is_one_baihe_protects(isolated_db, monkeypatch):
    monkeypatch.setenv("BAIHE_API_HOUSEHOLD_PORT", "8610")
    own = settings_service.baihe_own_ports()
    ports = _by_key(settings_service.baihe_ports(8600, 8610, PUBLIC))
    for key in ("api", "household", "extension"):
        assert ports[key]["port"] in own


def test_route_success_and_no_secrets(isolated_db):
    client = _local(ApiSettings(household_port=8610, public_url=PUBLIC, serve_frontend=False,
                                google_client_id="cid", google_client_secret="SECRET-VALUE"))
    r = client.get("/api/diagnostics/ports")
    assert r.status_code == 200
    ports = _by_key(r.json())
    assert ports["api"]["port"] == 8600
    assert ports["household"]["port"] == 8610 and ports["household"]["active"] is True
    text = json.dumps(r.json())
    for leak in ("SECRET-VALUE", "baihe.example.com", "://", "\\", "/home", "127.0.0.1"):
        assert leak not in text


def test_route_is_read_only(isolated_db):
    client = _local(ApiSettings(serve_frontend=False))
    assert client.post("/api/diagnostics/ports", json={}).status_code == 405


def test_pc_only_with_sign_in_on(isolated_db):
    app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
    remote = TestClient(app, base_url=PUBLIC, raise_server_exceptions=False)
    assert remote.get("/api/diagnostics/ports").status_code in (401, 403)
    admin = auth_service.grant_admin_local("admin@example.com")
    session = auth_service.create_session(admin["id"], "pytest", "203.0.113.9")
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
         api_auth.CSRF_HEADER: session["csrf_token"]}
    assert remote.get("/api/diagnostics/ports", headers=h).status_code == 403
    local = TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
    assert local.get("/api/diagnostics/ports").status_code == 200


def test_household_listener_refuses_it(isolated_db):
    base = ApiSettings(household_port=8610, public_url=PUBLIC, auth_mode="off", serve_frontend=False,
                       google_client_id="cid", google_client_secret="s")
    assert _local(base).get("/api/diagnostics/ports").status_code == 200
    app = create_app(base, listener="household")
    c = TestClient(app, base_url=PUBLIC, client=("127.0.0.1", 5000),
                   raise_server_exceptions=False)
    assert c.get("/api/diagnostics/ports").status_code in (401, 403)
