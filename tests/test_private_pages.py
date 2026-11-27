"""GET /private/<name>.html: admin only, one folder, plain names, sandboxed and never cached."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import portable
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service

PUBLIC_HOST = "baihe.example.com"
SIGN_IN = {"google_client_id": "cid", "google_client_secret": "s3cr3t-value",
           "public_url": f"https://{PUBLIC_HOST}"}
PAGE = b"<!doctype html><title>Grading</title><script>1</script>"


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(portable, "data_dir", lambda: str(tmp_path))
    (tmp_path / "private-pages").mkdir()
    (tmp_path / "private-pages" / "pack-1.html").write_bytes(PAGE)
    (tmp_path / "secret.html").write_bytes(b"outside")
    (tmp_path / "private-pages" / "notes.txt").write_text("x")
    return tmp_path / "private-pages"


def _household():
    settings = ApiSettings(household_port=8610, serve_frontend=False, **SIGN_IN)
    return TestClient(create_app(settings, listener="household"), base_url=f"http://{PUBLIC_HOST}",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False)


def _headers(session):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}"}


def _session(admin: bool, isolated_db):
    user = (auth_service.grant_admin_local("owner@example.com") if admin
            else auth_service.add_user("member@example.com"))
    return auth_service.create_session(user["id"], "pytest", "127.0.0.1")


def test_admin_opens_the_page_sandboxed_and_uncached(folder, isolated_db):
    r = _household().get("/private/pack-1.html", headers=_headers(_session(True, isolated_db)))
    assert r.status_code == 200 and r.content == PAGE
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    csp = r.headers["content-security-policy"]
    assert "sandbox allow-scripts" in csp and "allow-same-origin" not in csp
    assert "connect-src" not in csp and "default-src 'none'" in csp


def test_signed_out_is_refused(folder, isolated_db):
    assert _household().get("/private/pack-1.html").status_code == 401


def test_a_member_is_refused(folder, isolated_db):
    r = _household().get("/private/pack-1.html", headers=_headers(_session(False, isolated_db)))
    assert r.status_code == 403


@pytest.mark.parametrize("path", [
    "/private/", "/private", "/private/missing.html", "/private/notes.txt", "/private/pack-1",
    "/private/..%2Fsecret.html", "/private/%2e%2e/secret.html", "/private/..%5Csecret.html",
    "/private/pack-1.html%00", "/private/C:%5Csecret.html", "/private/pack-1.html::$DATA",
    "/private/.hidden.html", "/private/sub/pack-1.html",
])
def test_nothing_but_a_plain_html_file_is_served(path, folder, isolated_db):
    r = _household().get(path, headers=_headers(_session(True, isolated_db)))
    assert r.status_code in (404, 405) and b"outside" not in r.content


def test_a_link_out_of_the_folder_is_not_followed(folder, isolated_db):
    try:
        (folder / "link.html").symlink_to(folder.parent / "secret.html")
    except OSError:
        pytest.skip("no symlinks here")
    r = _household().get("/private/link.html", headers=_headers(_session(True, isolated_db)))
    assert r.status_code == 404 and b"outside" not in r.content


def test_pc_listener_serves_without_sign_in(folder, isolated_db):
    client = TestClient(create_app(ApiSettings(serve_frontend=False)), base_url="http://127.0.0.1:8600",
                        client=("127.0.0.1", 5000), raise_server_exceptions=False)
    r = client.get("/private/pack-1.html")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
