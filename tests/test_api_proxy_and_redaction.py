"""Proxy-header prefixes make a request non-local; the global error handler
strips local paths as well as keys. No network."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.auth import _is_local_scope
from api.server import create_app


@pytest.mark.parametrize("header", ["X-Forwarded-Port", "X-Forwarded-Prefix", "Forwarded-Extra",
                                    "X-Real-IP", "Tailscale-App-Capabilities", "Cf-Ipcountry"])
def test_unlisted_proxy_headers_make_a_request_non_local(header):
    assert _is_local_scope("127.0.0.1", {"host": "127.0.0.1:8600"})
    assert not _is_local_scope("127.0.0.1", {"host": "127.0.0.1:8600", header.lower(): "x"})


def test_service_error_message_hides_local_paths(isolated_db, monkeypatch):
    from services import library_service
    from services.service_errors import ServiceError

    def fail(**_):
        raise ServiceError(r"Could not read C:\Users\alice\Baihe\library.db")
    monkeypatch.setattr(library_service, "list_library_dramas", fail)
    client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                        headers={"X-Baihe-Local": "1"})
    resp = client.get("/api/library/dramas")
    assert resp.status_code == 500 and "alice" not in resp.text
