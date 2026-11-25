"""services/cast_service.py and /api/cast: a TV plays a title's subtitled copy by a short-lived link."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from lib.errors import ConflictError, NotFoundError
from services import artifact_service, cast_service, ownership_service


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
    assert client.get(f"/api/cast/dramas/{did}").status_code == 401
    assert client.head(f"/api/cast/video?k={secret}").status_code == 200


def test_a_deleted_title_ends_its_link_with_the_same_404(isolated_db):
    did = _title()
    secret = cast_service.create_link(None, did)["path"].split("k=")[1]
    db.delete_drama(did)
    with pytest.raises(NotFoundError) as ended:
        cast_service.resolve(secret)
    assert str(did) not in ended.value.message


def test_a_link_stops_when_its_account_loses_media_stream_or_is_deactivated(isolated_db):
    from services import auth_service
    uid = db.auth_create_user("tv@example.com")
    db.auth_grant_permission(uid, "media.stream")
    did = _title()
    principal = auth_service.member_principal(uid)
    secret = cast_service.create_link(principal, did)["path"].split("k=")[1]
    assert cast_service.resolve(secret)[2] == uid
    auth_service.revoke_permission(uid, "media.stream")
    with pytest.raises(NotFoundError):
        cast_service.resolve(secret)
    db.auth_grant_permission(uid, "media.stream")
    with pytest.raises(NotFoundError):     # a re-grant does not revive it
        cast_service.resolve(secret)
    secret = cast_service.create_link(principal, did)["path"].split("k=")[1]
    db.auth_update_user(uid, is_active=0)
    with pytest.raises(NotFoundError):
        cast_service.resolve(secret)


def test_signing_a_user_out_everywhere_ends_their_links(isolated_db):
    from services import auth_service
    uid = db.auth_create_user("tv2@example.com")
    db.auth_grant_permission(uid, "media.stream")
    did = _title()
    secret = cast_service.create_link(auth_service.member_principal(uid), did)["path"].split("k=")[1]
    assert cast_service.resolve(secret)
    auth_service.revoke_all_for_user(uid)
    with pytest.raises(NotFoundError):
        cast_service.resolve(secret)


def test_a_link_cannot_be_made_for_a_title_the_member_view_cannot_see(isolated_db):
    from services import auth_service
    uid = db.auth_create_user("tv3@example.com")
    other = db.auth_create_user("owner@example.com")
    db.auth_grant_permission(other, "media.stream")
    did = _title()
    ownership_service.set_owner(None, "drama", did, other)
    ownership_service.set_private(None, "drama", did, True)
    member = auth_service.member_principal(uid)
    with pytest.raises(NotFoundError):
        cast_service.create_link(member, did)
    owner_link = cast_service.create_link(auth_service.member_principal(other), did)["path"].split("k=")[1]
    assert cast_service.resolve(owner_link)
    ownership_service.set_private(None, "drama", did, False)
    assert cast_service.create_link(member, did)


def test_the_tv_fetch_counts_against_the_accounts_stream_cap(isolated_db, monkeypatch):
    from api.routers import media_routes
    from services import auth_service
    uid = db.auth_create_user("tv4@example.com")
    db.auth_grant_permission(uid, "media.stream")
    did = _title()
    secret = cast_service.create_link(auth_service.member_principal(uid), did)["path"].split("k=")[1]
    client = TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False)),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    monkeypatch.setattr(media_routes, "MAX_STREAMS_PER_USER", 0)
    assert client.get(f"/api/cast/video?k={secret}").status_code == 429
