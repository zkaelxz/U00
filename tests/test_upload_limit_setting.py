"""The saved "Upload size limit (MB)" setting and how max_upload_bytes() reads it."""

import io
import os

import pytest

import db
from services import media_upload_service as mus
from services import settings_service
from services.service_errors import InvalidInputError

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("BAIHE_MAX_UPLOAD_MB", raising=False)


def test_default_is_20_gb(isolated_db):
    assert mus.max_upload_bytes() == 20480 * MB
    assert not mus.upload_limit_from_env()


def test_saved_setting_is_used(isolated_db):
    settings_service.set_settings({"max_upload_mb": 4096})
    assert mus.max_upload_bytes() == 4096 * MB


def test_env_wins_over_saved_setting(isolated_db, monkeypatch):
    settings_service.set_settings({"max_upload_mb": 4096})
    monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", "500")
    overview = settings_service.get_settings_overview()
    assert mus.max_upload_bytes() == 500 * MB
    assert overview["upload_max_mb_from_env"] is True
    assert overview["effective_upload_max_mb"] == 500
    assert overview["preferences"]["max_upload_mb"] == 4096


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "", "nan", "inf"])
def test_unusable_env_falls_back_to_saved_setting(isolated_db, monkeypatch, raw):
    settings_service.set_settings({"max_upload_mb": 300})
    monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", raw)
    assert mus.max_upload_bytes() == 300 * MB
    assert not mus.upload_limit_from_env()


def test_none_resets_to_default(isolated_db):
    settings_service.set_settings({"max_upload_mb": 300})
    settings_service.set_settings({"max_upload_mb": None})
    assert mus.max_upload_bytes() == 20480 * MB


def test_garbage_stored_value_reads_as_default(isolated_db):
    db.set_app_setting("pref.max_upload_mb", "lots")
    assert mus.max_upload_bytes() == 20480 * MB
    db.set_app_setting("pref.max_upload_mb", 0)
    assert mus.max_upload_bytes() == 20480 * MB


def test_bounds_accepted(isolated_db):
    for mb in (100, 1_048_576):
        settings_service.set_settings({"max_upload_mb": mb})
        assert mus.max_upload_bytes() == mb * MB
    with pytest.raises(InvalidInputError, match="100 to 1048576"):
        settings_service.set_settings({"max_upload_mb": 99})


def test_too_large_message_names_limit_and_setting():
    msg = mus.too_large_message(2048 * MB)
    assert "2048 MB" in msg and "Settings > Advanced > Uploads" in msg


def test_upload_refused_before_copy_when_disk_is_full(isolated_db, monkeypatch):
    did = db.create_drama(title_en="D")
    free = 10 * MB
    monkeypatch.setattr(mus.shutil, "disk_usage",
                        lambda _p: type("U", (), {"free": free})())
    with pytest.raises(InvalidInputError, match="not enough free disk space"):
        mus.upload_media(did, "a.mp3", io.BytesIO(b"x" * (free + 1)))
    assert os.listdir(db.drama_dir(did)) == []


def test_unreadable_disk_space_does_not_block(isolated_db, monkeypatch):
    did = db.create_drama(title_en="D")

    def boom(_p):
        raise OSError
    monkeypatch.setattr(mus.shutil, "disk_usage", boom)
    assert mus.upload_media(did, "a.mp3", io.BytesIO(b"x" * 10))["size"] == 10


def _client(local):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    app = create_app(ApiSettings(auth_mode="off", serve_frontend=False))
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _household_client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    app = create_app(ApiSettings(household_port=8610, google_client_id="cid",
                                 google_client_secret="s",
                                 public_url="https://baihe.example.com"),
                     listener="household")
    return TestClient(app, base_url="https://baihe.example.com", client=("203.0.113.9", 5000),
                      raise_server_exceptions=False)


def test_settings_route_saves_and_reads_back_numbers_only(isolated_db, monkeypatch):
    c = _client(local=True)
    r = c.post("/api/settings", json={"max_upload_mb": 5000})
    assert r.status_code == 200
    body = r.json()
    assert body["preferences"]["max_upload_mb"] == 5000
    assert body["effective_upload_max_mb"] == 5000 and body["upload_max_mb_from_env"] is False
    monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", "777")
    body = c.get("/api/settings").json()
    assert body["effective_upload_max_mb"] == 777 and body["upload_max_mb_from_env"] is True
    assert "BAIHE_MAX_UPLOAD_MB" not in str(body)


def test_settings_route_rejects_bad_value_with_clear_text(isolated_db):
    r = _client(local=True).post("/api/settings", json={"max_upload_mb": 5})
    assert r.status_code == 422
    assert "100 to 1048576" in r.text


def test_huge_env_value_is_clamped_not_an_overflow(isolated_db, monkeypatch):
    monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", "1e303")
    assert mus.max_upload_bytes() == settings_service.MAX_UPLOAD_MB * MB
    assert _client(local=True).get("/api/settings").status_code == 200


def test_household_listener_refuses_every_upload_route(isolated_db):
    did = db.create_drama(title_en="D")
    c = _household_client()
    files = {"file": ("a.mp3", b"x" * 10)}
    calls = [
        ("/api/settings", {"json": {"max_upload_mb": 5000}}),
        (f"/api/media/dramas/{did}/upload", {"files": files}),
        (f"/api/media/dramas/{did}/upload-and-transcribe", {"files": files}),
        (f"/api/media/dramas/{did}/download-url", {"json": {"url": "https://example.com/a.mp3"}}),
        ("/api/backups/import/list", {"files": files}),
        ("/api/backups/import", {"files": files}),
        ("/api/library/admin/restore", {"files": files, "data": {"confirm": "true",
                                                           "confirm_text": "RESTORE"}}),
    ]
    for path, kw in calls:
        assert c.post(path, **kw).status_code in (401, 403), path
    assert settings_service.get_preference("max_upload_mb") == 20480


def test_only_local_only_routes_reach_max_upload_bytes():
    """The setting has no household cap because the household listener can't
    upload at all; a new caller outside these modules must be reviewed."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    callers = {str(p.relative_to(root)).replace(os.sep, "/")
               for folder in ("api", "services") for p in (root / folder).rglob("*.py")
               if "max_upload_bytes" in p.read_text(encoding="utf-8")}
    assert callers == {
        "api/routers/backup_routes.py", "api/routers/library_admin_routes.py",
        "services/backup_import_service.py", "services/media_upload_service.py",
        "services/settings_service.py", "services/url_media_service.py"}
    from api import auth as api_auth
    from api.api_config import ApiSettings
    from api.server import create_app
    app = create_app(ApiSettings(auth_mode="off", serve_frontend=False))
    upload_routes = {"/api/backups/import/list", "/api/backups/import",
                     "/api/library/admin/restore"}
    seen = 0
    for _r, path, _m, decls in api_auth.iter_route_declarations(app):
        if path in upload_routes or (path.startswith("/api/media/dramas/") and
                                     path.rsplit("/", 1)[-1] in ("upload", "upload-and-transcribe",
                                                                 "download-url")):
            seen += 1
            assert [d[0] for d in decls] == ["local_only"], path
    assert seen == 6


def test_restore_is_capped_below_the_upload_limit(isolated_db, monkeypatch):
    from services import library_admin_service as las
    monkeypatch.setattr(las, "RESTORE_MAX_UPLOAD_BYTES", 1000)
    r = _client(local=True).post(
        "/api/library/admin/restore", files={"file": ("b.zip", b"x" * 2000)},
        data={"confirm": "true", "confirm_text": "RESTORE"}, headers={"X-Baihe-Local": "1"})
    assert r.status_code == 422 and "at most" in r.text


def test_backup_import_upload_refused_when_disk_is_full(isolated_db, monkeypatch, tmp_path):
    from services import backup_import_service as bis
    monkeypatch.setattr(mus.shutil, "disk_usage", lambda _p: type("U", (), {"free": MB})())
    with pytest.raises(InvalidInputError, match="not enough free disk space"):
        bis._save_upload(io.BytesIO(b"x" * (2 * MB)), str(tmp_path / "up.zip"))
