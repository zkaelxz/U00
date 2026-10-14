"""Export file names (single safe path segment, CJK fallback), empty-export
refusals, the zip layout, and the friendly storage-error messages."""
import errno
import io
import sqlite3
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import APIRouter
from fastapi.testclient import TestClient

from api import error_handlers
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import export_service, library_backup_service
from services.service_errors import ConflictError

HOSTILE_TITLES = ["../../etc/passwd", "..", "...", "a/b\\c", "CON.txt", "x\x00y.srt",
                  "‮txt.srt", "日本語" * 80, " . ", "NUL"]


def _drama(db, title, **kw):
    return db.create_drama(title_en=title, title_zh="", **kw)


@pytest.mark.parametrize("title", HOSTILE_TITLES)
def test_names_stay_one_safe_segment(isolated_db, title):
    did = _drama(isolated_db, title, episode_number=3)
    for name in (export_service.download_filename(did, "subtitles", "en", "srt"),
                 export_service.folder_name(did)):
        assert "/" not in name and "\\" not in name and "\x00" not in name
        assert name not in ("", ".", "..") and not name.startswith("..")
        assert len(name.encode("utf-8")) <= 255


def test_cjk_fallback_is_readable_and_has_no_leading_dot():
    header = export_service.content_disposition("日本語 - Ep 1 - subtitles (zh).srt")
    assert 'filename="Ep 1 - subtitles (zh).srt"' in header
    assert 'filename="download.srt"' in export_service.content_disposition("日本語.srt")
    assert 'filename="hidden.srt"' in export_service.content_disposition("...hidden.srt")
    assert "__" not in export_service.content_disposition("日本語ドラマ.srt").split(";")[1]


def test_zero_line_and_untranslated_exports_are_refused(isolated_db):
    empty = _drama(isolated_db, "Empty")
    with pytest.raises(ConflictError, match="no subtitle lines"):
        export_service.generate_subtitle_text(empty, "srt", "en")
    raw = _drama(isolated_db, "Raw")
    isolated_db.save_lines(raw, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
    with pytest.raises(ConflictError, match="no translated lines"):
        export_service.generate_subtitle_text(raw, "srt", "en")
    assert "你好" in export_service.generate_subtitle_text(raw, "srt", "zh")


def _zip_names(isolated_db, titles):
    ids = []
    for i, t in enumerate(titles):
        did = _drama(isolated_db, t, episode_number=1, status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
        ids.append(did)
    captured = {}
    real = zipfile.ZipFile

    class Spy(real):
        def writestr(self, item, data, *a, **k):
            captured.setdefault("names", []).append(item)
            return super().writestr(item, data, *a, **k)

    library_backup_service.zipfile.ZipFile = Spy
    try:
        library_backup_service._export_job("job-y", ids)
    finally:
        library_backup_service.zipfile.ZipFile = real
    return captured["names"]


def test_zip_entries_are_named_like_single_downloads(isolated_db):
    names = _zip_names(isolated_db, ["Show", "Show", "../..", "日本語" * 100])
    assert "Show - Ep 1/Show - Ep 1 - subtitles (en).srt" in names
    assert "Show - Ep 1/Show - Ep 1 - bilingual subtitles (zh+en).srt" in names
    for n in names:
        parts = n.split("/")
        assert len(parts) == 2 and ".." not in parts and all(p and "\\" not in p for p in parts)
        assert all(len(p.encode("utf-8")) <= 255 for p in parts)
    folders = {n.split("/")[0] for n in names}
    assert len(folders) == 4  # the duplicate title got its own folder


def _client_raising(exc):
    app = create_app(ApiSettings())
    router = APIRouter()

    @router.get("/api/_boom")
    def boom():
        raise exc

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("exc, status, code, message", [
    (OSError(errno.ENOSPC, "No space left on device: /secret/path"), 507, "disk_full",
     "The disk is full."),
    (sqlite3.OperationalError("database or disk is full"), 507, "disk_full", "The disk is full."),
    (sqlite3.OperationalError("attempt to write a readonly database"), 503, "library_read_only",
     "The library folder is read-only."),
    (OSError(errno.EROFS, "Read-only file system: /secret/path"), 503, "library_read_only",
     "The library folder is read-only."),
    (sqlite3.DatabaseError("database disk image is malformed"), 500, "database_corrupt",
     "The library database looks damaged; restore from a backup."),
])
def test_storage_errors_get_fixed_messages(isolated_db, exc, status, code, message):
    r = _client_raising(exc).get("/api/_boom")
    assert r.status_code == status
    assert r.json() == {"error": {"code": code, "message": message}}
    assert "secret" not in r.text


def test_other_errors_stay_generic(isolated_db):
    r = _client_raising(OSError(errno.EIO, "boom")).get("/api/_boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"


def test_unbuilt_frontend_serves_a_plain_page_and_logs(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        app = create_app(ApiSettings(), frontend_dist=tmp_path / "missing")
    assert "The web app is not built; run npm run build" in caplog.text
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/")
    assert r.status_code == 200 and "npm run build" in r.text
    assert c.get("/api/nope").json()["error"]["code"] == "not_found"
