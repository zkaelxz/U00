"""services/cast_service.py and /api/cast: a TV plays a title's subtitled copy by a short-lived link."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from lib.errors import ConflictError, NotFoundError
from services import artifact_service, cast_service


@pytest.fixture(autouse=True)
def _clean_links():
    cast_service._links.clear()
    yield
    cast_service._links.clear()


def _title(name="burned_video_1.mp4", language="en", body=b"0123456789"):
    did = db.create_drama(title_en="Ep", title_zh="题")
    path = artifact_service.output_path(did, "video", name)
    with open(path, "wb") as f:
        f.write(body)
    if language:
        artifact_service.set_download_language(did, "video", name, language)
    return did


def test_status_follows_the_copy(isolated_db):
    did = db.create_drama(title_en="Ep", title_zh="题")
    assert cast_service.status(did)["state"] == "missing"
    assert cast_service.status(_title())["state"] == "ready"
    assert cast_service.status(_title("x.mkv"))["state"] == "unsupported"
    assert cast_service.status(_title(language="zh"))["state"] == "missing"


def test_a_link_is_refused_until_the_copy_is_ready(isolated_db):
    did = db.create_drama(title_en="Ep", title_zh="题")
    with pytest.raises(ConflictError):
        cast_service.create_link(None, did)


def test_a_link_resolves_until_it_expires(isolated_db, monkeypatch):
    did = _title()
    link = cast_service.create_link(None, did)
    secret = link["path"].split("k=")[1]
    assert cast_service.resolve(secret)[1] == "video/mp4"
    now = cast_service.time.time()
    monkeypatch.setattr(cast_service.time, "time", lambda: now + cast_service.LINK_SECONDS + 1)
    with pytest.raises(NotFoundError):
        cast_service.resolve(secret)
    with pytest.raises(NotFoundError):
        cast_service.resolve("not-a-link")


def test_only_the_newest_links_of_a_user_stay_live(isolated_db):
    did = _title()
    secrets_ = [cast_service.create_link(None, did)["path"].split("k=")[1]
                for _ in range(cast_service.MAX_LINKS_PER_USER + 1)]
    with pytest.raises(NotFoundError):
        cast_service.resolve(secrets_[0])
    assert cast_service.resolve(secrets_[-1])


def test_the_tv_fetches_the_file_with_no_sign_in_and_ranges(isolated_db):
    did = _title(body=b"0123456789")
    client = TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False)),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    secret = cast_service.create_link(None, did)["path"].split("k=")[1]
    got = client.get(f"/api/cast/video?k={secret}", headers={"Range": "bytes=2-4"})
    assert got.status_code == 206 and got.content == b"234"
    assert got.headers["access-control-allow-origin"] == "*"
    assert client.get("/api/cast/video?k=" + "x" * 43).status_code == 404
    assert client.get(f"/api/cast/dramas/{did}").status_code in (401, 403)
