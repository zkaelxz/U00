"""The Android shell's update routes: the build the server holds, and who may fetch it."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api.api_config import ApiSettings
from api.server import create_app
from services import android_app_service as svc
from services import auth_service
from fastapi.testclient import TestClient

from tests.api_helpers import loopback_client, session_headers

VERSION = "/api/app/android"
APK = "/api/app/android/apk"
REMOTE = "https://baihe.example.com"


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    (tmp_path / "android").mkdir()
    return tmp_path / "android"


def _publish(folder, code="7"):
    (folder / svc.APK_NAME).write_bytes(b"PK-apk-bytes")
    (folder / svc.VERSION_NAME).write_text(code)


def test_open_app_reports_the_published_build(isolated_db, folder):
    _publish(folder)
    client = loopback_client(create_app(ApiSettings()))
    assert client.get(VERSION).json() == {"version_code": 7}
    got = client.get(APK)
    assert got.status_code == 200 and got.content == b"PK-apk-bytes"
    assert got.headers["content-type"] == "application/vnd.android.package-archive"
    assert got.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("code", ["", "abc", "0", "-3", "99999999999"])
def test_unusable_version_file_means_no_build(isolated_db, folder, code):
    _publish(folder, code)
    client = loopback_client(create_app(ApiSettings()))
    assert client.get(VERSION).json() == {"version_code": None}
    assert client.get(APK).status_code == 404


def test_no_build_published(isolated_db, folder):
    client = loopback_client(create_app(ApiSettings()))
    assert client.get(VERSION).json() == {"version_code": None}
    assert client.get(APK).status_code == 404


def test_apk_without_version_file_is_not_offered(isolated_db, folder):
    (folder / svc.APK_NAME).write_bytes(b"x")
    client = loopback_client(create_app(ApiSettings()))
    assert client.get(VERSION).json() == {"version_code": None}
    assert client.get(APK).status_code == 404


def test_sign_in_required_when_sign_in_is_on(isolated_db, folder):
    _publish(folder)
    app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
    anon = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    assert anon.get(APK).status_code in (401, 403)
    user = auth_service.add_user("kid@example.com")
    s = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
    got = TestClient(app, base_url=REMOTE, raise_server_exceptions=False).get(APK, headers=session_headers(s))
    assert got.status_code == 200, got.text
