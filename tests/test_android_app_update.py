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


# --- fetching the build the workflow published ---------------------------------

import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import zipfile  # noqa: E402

from lib.errors import ConflictError, DependencyUnavailableError, NotFoundError  # noqa: E402
from tests.test_update_service import http  # noqa: E402,F401

TAG_URL = f"https://api.github.com/repos/zkaelxz/U00/releases/tags/{svc.RELEASE_TAG}"
BASE = "https://github.com/zkaelxz/U00/releases/download/android-app/"


def _apk_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AndroidManifest.xml", "x")
    return buf.getvalue()


def _serve(http, apk=None, code=b"1042", digest=None, assets=(svc.APK_NAME, svc.SHA_NAME, svc.VERSION_NAME)):
    apk = _apk_bytes() if apk is None else apk
    digest = digest or hashlib.sha256(apk).hexdigest()
    listed = [{"name": n, "size": len(apk), "browser_download_url": BASE + n} for n in assets]
    http.add(TAG_URL, body=json.dumps({"assets": listed}).encode())
    http.add(BASE + svc.APK_NAME, body=apk)
    http.add(BASE + svc.SHA_NAME, body=f"{digest}  {svc.APK_NAME}\n".encode())
    http.add(BASE + svc.VERSION_NAME, body=code)
    return apk


def test_fetch_puts_the_published_build_where_the_phone_finds_it(http, folder):
    apk = _serve(http)
    assert svc.fetch_latest() == {"version_code": 1042, "changed": True}
    assert (folder / svc.APK_NAME).read_bytes() == apk
    assert svc.latest_version_code() == 1042
    assert not (folder / (svc.APK_NAME + ".new")).exists()


def test_fetch_changes_nothing_when_the_server_already_holds_that_build(http, folder):
    _publish(folder, "1042")
    _serve(http)
    assert svc.fetch_latest() == {"version_code": 1042, "changed": False}
    assert (folder / svc.APK_NAME).read_bytes() == b"PK-apk-bytes"


def test_fetch_never_replaces_a_build_with_an_older_one(http, folder):
    _publish(folder, "2000")
    _serve(http)
    assert svc.fetch_latest() == {"version_code": 2000, "changed": False}


def test_a_wrong_checksum_keeps_the_old_build(http, folder):
    _publish(folder, "7")
    _serve(http, digest="0" * 64)
    with pytest.raises(ConflictError):
        svc.fetch_latest()
    assert svc.latest_version_code() == 7
    assert (folder / svc.APK_NAME).read_bytes() == b"PK-apk-bytes"
    assert not (folder / (svc.APK_NAME + ".new")).exists()


def test_a_file_that_is_not_an_apk_is_refused(http, folder):
    _serve(http, apk=b"<html>not an apk</html>")
    with pytest.raises(ConflictError):
        svc.fetch_latest()
    assert svc.latest_version_code() is None


@pytest.mark.parametrize("code", [b"", b"abc", b"0", b"-3", b"99999999999"])
def test_an_unusable_published_version_is_refused(http, folder, code):
    _serve(http, code=code)
    with pytest.raises(DependencyUnavailableError):
        svc.fetch_latest()
    assert svc.latest_version_code() is None


def test_a_release_missing_a_file_is_refused(http, folder):
    _serve(http, assets=(svc.APK_NAME, svc.VERSION_NAME))
    with pytest.raises(DependencyUnavailableError):
        svc.fetch_latest()


def test_nothing_published_yet_says_how_to_publish(http, folder):
    http.add(TAG_URL, status=404)
    with pytest.raises(NotFoundError) as caught:
        svc.fetch_latest()
    assert "Android APK" in caught.value.message


def test_fetch_route_is_pc_only_and_returns_numbers_only(http, folder):
    _serve(http)
    app = create_app(ApiSettings())
    got = loopback_client(app).post("/api/system/android-app/fetch", json={})
    assert got.status_code == 200 and got.json() == {"version_code": 1042, "changed": True}
    away = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    assert away.post("/api/system/android-app/fetch", json={}).status_code in (401, 403)
