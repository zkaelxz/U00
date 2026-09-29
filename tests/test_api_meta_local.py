"""
GET /api/meta `local`: true exactly when a PC-only (local_only) route would
let the same request through. The React UI uses it to hide PC-only
controls; the routes still enforce it themselves.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app


def _client(auth_mode, base_url, client=("testclient", 50000)):
    return TestClient(create_app(ApiSettings(auth_mode=auth_mode)), base_url=base_url,
                      client=client, raise_server_exceptions=False)


def test_auth_off_is_local(isolated_db):
    body = _client("off", "http://127.0.0.1:8600", ("127.0.0.1", 5000)).get("/api/meta").json()
    assert body["local"] is True
    assert body["app"] == "Baihe Studio"


def test_auth_on_loopback_is_local(isolated_db):
    r = _client("on", "http://127.0.0.1:8600", ("127.0.0.1", 5000)).get("/api/meta")
    assert r.status_code == 200
    assert r.json()["local"] is True


def test_auth_on_remote_is_not_local(isolated_db):
    r = _client("on", "https://baihe.example.com").get("/api/meta")
    assert r.status_code == 200, r.text
    assert r.json()["local"] is False


def test_auth_on_loopback_peer_behind_proxy_is_not_local(isolated_db):
    # A reverse proxy on the same PC connects from 127.0.0.1 but adds
    # forwarding headers; local_only() refuses that, so `local` is false.
    c = _client("on", "http://127.0.0.1:8600", ("127.0.0.1", 5000))
    r = c.get("/api/meta", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 200
    assert r.json()["local"] is False


def test_local_agrees_with_local_only_route(isolated_db):
    for base, peer in (("http://127.0.0.1:8600", ("127.0.0.1", 5000)),
                       ("https://baihe.example.com", ("testclient", 50000))):
        c = _client("on", base, peer)
        local = c.get("/api/meta").json()["local"]
        # A local_only GET: artifact download (404 when allowed and absent, 403 when refused).
        status = c.get("/api/library/admin/artifacts/backup").status_code
        assert (status != 403) == local, (base, status)
