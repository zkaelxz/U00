"""Export file names (single safe path segment, CJK fallback), empty-export
refusals, the zip layout, and the friendly storage-error messages."""
import errno
import io
import sqlite3
import zipfile

import pytest

from lib import storage_errors
from lib.safe_paths import is_device_name

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


def test_cues_are_written_in_time_order_after_a_retime(isolated_db):
    did = _drama(isolated_db, "Retimed")
    isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="一", en="A"),
                                 Line(idx=1, start=10.0, end=12.0, zh="二", en="B"),
                                 Line(idx=2, start=5.0, end=7.0, zh="三", en="C")])
    result = export_service.generate_subtitle_export(did, "srt", "en", tidy_timing=True, fps=24.0)
    blocks = [b.split("\n") for b in result["text"].strip().split("\n\n")]
    assert [b[2] for b in blocks] == ["A", "C", "B"]
    assert [b[0] for b in blocks] == ["1", "2", "3"]
    assert result["cues_trimmed"] == 0
    assert "00:00:12,000" in result["text"]


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


def _sharing_violation():
    # What Windows raises when another program holds the file open: EACCES,
    # told apart from a refused permission only by its winerror.
    exc = PermissionError(errno.EACCES, "The process cannot access the file: /secret/path")
    exc.winerror = 32
    return exc


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
    (PermissionError(errno.EACCES, "Permission denied: /secret/path"), 503, "permission_denied",
     "Permission denied: Baihe is not allowed to open a file it needs, or another program has it "
     "open. The app log names the file."),
    (OSError(errno.EPERM, "Operation not permitted: /secret/path"), 503, "permission_denied",
     "Permission denied: Baihe is not allowed to open a file it needs, or another program has it "
     "open. The app log names the file."),
    (_sharing_violation(), 409, "file_in_use",
     "A file Baihe needs is open in another program. Close it and try again."),
    (sqlite3.OperationalError("database is locked"), 503, "library_busy",
     "The library database is busy. Try again in a moment."),
])
def test_storage_errors_get_fixed_messages(isolated_db, exc, status, code, message):
    r = _client_raising(exc).get("/api/_boom")
    assert r.status_code == status
    assert r.json() == {"error": {"code": code, "message": message}}
    assert "secret" not in r.text


def test_storage_error_is_logged_with_the_file_name(isolated_db, monkeypatch):
    import applog
    logged = []
    monkeypatch.setattr(applog, "get_logger", lambda: type(
        "Log", (), {"error": lambda self, fmt, *a: logged.append(fmt % a)})())
    exc = PermissionError(errno.EACCES, "Permission denied", "/home/someone/lib/dramas/7/audio.wav")
    r = _client_raising(exc).get("/api/_boom")
    assert r.json()["error"]["code"] == "permission_denied"
    assert "audio.wav" not in r.text
    assert len(logged) == 1
    assert "permission_denied on GET /api/_boom" in logged[0] and "audio.wav" in logged[0]
    assert "someone" not in logged[0]


@pytest.mark.parametrize("exc_type, text, code", [
    ("PermissionError", "[Errno 13] Permission denied: 'C:\\x\\a.wav'", "permission_denied"),
    ("PermissionError", "[WinError 32] The process cannot access the file", "file_in_use"),
    ("PermissionError", "[WinError 5] Access is denied: 'C:\\x'", "permission_denied"),
    ("OSError", "[Errno 28] No space left on device", "disk_full"),
    ("OSError", "[WinError 112] There is not enough space on the disk", "disk_full"),
    ("OperationalError", "database is locked", "library_busy"),
    ("OSError", "[Errno 5] Input/output error", None),
    ("RuntimeError", "[Errno 13] looks like one", None),
    ("ValueError", "Permission denied", None),
])
def test_worker_errors_known_only_by_text_are_classified(exc_type, text, code):
    problem = storage_errors.classify_text(exc_type, text)
    assert (problem.code if problem else None) == code


def test_opening_a_folder_as_a_file_is_not_called_permission_denied(tmp_path):
    exc = PermissionError(errno.EACCES, "Permission denied", str(tmp_path))
    assert storage_errors.classify(exc) is None


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


@pytest.mark.parametrize("title", ["CON", "nul", "Aux", "COM1", "con.txt"])
def test_device_names_get_a_guard_even_without_an_episode(isolated_db, title):
    did = _drama(isolated_db, title)
    assert export_service.folder_name(did).startswith("_")
    assert not is_device_name(export_service.download_filename(did, "subtitles", "en", "srt"))


def test_device_guard_survives_the_ascii_fallback():
    assert 'filename="_CON.srt"' in export_service.content_disposition("_CON.srt")


def test_zip_folders_never_collide_case_insensitively(isolated_db):
    first = _drama(isolated_db, "Love", episode_number=1, status="translated")
    ids = [first]
    for t in ("love", "LOVE"):
        ids.append(_drama(isolated_db, t, episode_number=1, status="translated"))
    # a title that already looks like the id-suffixed name of the first
    ids.append(_drama(isolated_db, f"Love - Ep 1 [{ids[1]}]", status="translated"))
    names = _zip_names_for(isolated_db, ids)
    folders = [n.split("/")[0] for n in names]
    assert len({f.casefold() for f in set(folders)}) == len(ids)


def _zip_names_for(isolated_db, ids):
    for did in ids:
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
    captured = []
    real = zipfile.ZipFile

    class Spy(real):
        def writestr(self, item, data, *a, **k):
            captured.append(item)
            return super().writestr(item, data, *a, **k)

    library_backup_service.zipfile.ZipFile = Spy
    try:
        library_backup_service._export_job("job-z", ids)
    finally:
        library_backup_service.zipfile.ZipFile = real
    return captured


def test_storage_error_text_is_logged_redacted_not_returned(isolated_db, caplog):
    with caplog.at_level("ERROR"):
        r = _client_raising(sqlite3.DatabaseError("database disk image is malformed")).get("/api/_boom")
    assert "malformed" not in r.text
