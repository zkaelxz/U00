"""A member given the upload right may send audio/video from away, held to the member limit."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import db
from services import auth_service, media_upload_service, settings_service
from tests.test_api_household_listener import _admin_session, _h, _household_client, _remote_uploads

MB = 1024 * 1024


def _member(email="kid@example.com", right=True):
    user = auth_service.add_user(email)
    if right:
        auth_service.grant_permission(user["id"], auth_service.MEMBER_UPLOAD_PERMISSION)
    return user, auth_service.create_session(user["id"], "pytest", "203.0.113.9")


def _own_title(user):
    return db.create_drama(title_en="Mine", source_language="zh", owner_user_id=user["id"])


def _upload(session, did, size):
    files = {"file": ("a.wav", b"0" * size, "audio/wav")}
    return _household_client().post(f"/api/media/dramas/{did}/upload", files=files, headers=_h(session))


@pytest.fixture
def stub_upload(monkeypatch):
    seen = []

    def fake(drama_id, name, fileobj, **kw):
        seen.append(kw["principal"])
        return {"name": "source.wav", "size": 1, "kind": "audio", "job_id": None}
    monkeypatch.setattr(media_upload_service, "upload_media", fake)
    return seen


def test_default_member_limit_is_1_gb(isolated_db):
    assert media_upload_service.member_upload_bytes() == 1024 * MB


def test_member_limit_never_exceeds_the_pc_limit(isolated_db):
    settings_service.set_settings({"max_upload_mb": 500, "member_upload_mb": 2000})
    member = {"is_admin": False}
    assert media_upload_service.upload_limit_bytes(member) == 500 * MB
    assert media_upload_service.upload_limit_bytes({"is_admin": True}) == 500 * MB
    assert media_upload_service.upload_limit_bytes(None) == 500 * MB


def test_admin_is_not_held_to_the_member_limit(isolated_db):
    settings_service.set_settings({"member_upload_mb": 100})
    assert media_upload_service.upload_limit_bytes({"is_admin": True}) == 20480 * MB
    assert media_upload_service.upload_limit_bytes({"is_admin": False}) == 100 * MB


def test_member_without_the_right_is_refused(isolated_db, stub_upload):
    user, s = _member(right=False)
    assert _upload(s, _own_title(user), 10).status_code == 403
    assert stub_upload == []


def test_member_with_the_right_uploads_to_their_own_title_without_the_admin_switch(isolated_db, stub_upload):
    _remote_uploads(on=False)
    user, s = _member()
    r = _upload(s, _own_title(user), 10)
    assert r.status_code == 200
    assert stub_upload[0]["is_admin"] is False


def test_member_over_the_limit_is_refused_before_the_body_is_read(isolated_db, stub_upload):
    settings_service.set_settings({"member_upload_mb": 50})
    user, s = _member()
    r = _upload(s, _own_title(user), 60 * MB)
    assert r.status_code == 413
    assert "limit for uploads from members" in r.text
    assert stub_upload == []


def test_member_cannot_use_the_other_upload_routes(isolated_db):
    user, s = _member()
    did = _own_title(user)
    files = {"file": ("c.png", b"0" * 10, "image/png")}
    assert _household_client().post(f"/api/dramas/{did}/cover", files=files, headers=_h(s)).status_code == 403


def test_member_cannot_upload_into_someone_elses_private_title(isolated_db, stub_upload):
    owner = auth_service.add_user("owner2@example.com")
    did = db.create_drama(title_en="Theirs", source_language="zh", owner_user_id=owner["id"], is_private=1)
    _user, s = _member()
    assert _upload(s, did, 10).status_code in (403, 404)
    assert stub_upload == []


def test_admin_away_loses_the_member_right_when_the_switch_is_off(isolated_db, stub_upload):
    _remote_uploads(on=False)
    s = _admin_session()
    did = db.create_drama(title_en="T", source_language="zh")
    assert _upload(s, did, 10).status_code == 403
    _remote_uploads()
    assert _upload(s, did, 10).status_code == 200


def test_media_status_shows_the_members_own_limit(isolated_db):
    settings_service.set_settings({"member_upload_mb": 200})
    user, s = _member()
    did = _own_title(user)
    r = _household_client().get(f"/api/media/dramas/{did}/status", headers=_h(s))
    assert r.status_code == 200 and r.json()["upload_max_mb"] == 200


def test_service_stops_a_member_stream_at_the_member_limit(isolated_db):
    import io
    from lib.errors import InvalidInputError
    settings_service.set_settings({"member_upload_mb": 50})
    user = auth_service.add_user("kid@example.com")
    did = db.create_drama(title_en="T", content_mode="audio_drama", owner_user_id=user["id"])
    body = io.BytesIO(b"0" * (60 * MB))
    with pytest.raises(InvalidInputError, match="members"):
        media_upload_service.upload_media(did, "a.wav", body, principal={"is_admin": False, "user_id": user["id"]})


def test_setting_range_is_validated(isolated_db):
    with pytest.raises(Exception):
        settings_service.set_settings({"member_upload_mb": 10})


def test_member_cannot_replace_media_on_a_shared_or_pc_made_title(isolated_db):
    """Visible and editable is not enough: the service insists on the member's own title."""
    other = auth_service.add_user("other@example.com")
    shared = db.create_drama(title_en="Shared", source_language="zh", owner_user_id=other["id"], is_private=0)
    pc_made = db.create_drama(title_en="PC", source_language="zh")
    user, s = _member()
    for did in (shared, pc_made):
        r = _upload(s, did, 10)
        assert r.status_code == 403 and "titles you made" in r.text
