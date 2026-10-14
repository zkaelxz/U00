"""The multipart upload prelude every upload route shares: a chunked body is
refused, a missing or unreadable Content-Length is refused, one over the
route's cap is 413 before any of the body is read, a body longer than its
Content-Length is cut off at the cap, and a form the parser can't read gets
the route's own error. Requests go straight to the ASGI app so the headers
are exactly the ones a test sets."""
import asyncio

import pytest

pytest.importorskip("fastapi")

import db
import subtitle_parse
from api.api_config import ApiSettings
from api.routers import bug_report_routes
from api.server import create_app
from services import auth_service, media_upload_service, page_import_limits

MB = 1024 * 1024
UPLOAD_HELP = "An upload needs a Content-Length."
BACKUP_HELP = ("Send the backup as multipart/form-data: a 'file' part, plus drama_ids, "
               "confirm and confirm_text for an import.")
SUBTITLE_HELP = ("Send the subtitle file as multipart/form-data in a 'file' field; options "
                 "(mode, split_bilingual, translation_first, encoding) are text fields.")


def _backup_too_large():
    return media_upload_service.too_large_message(media_upload_service.max_upload_bytes())


# path, chunked message, missing-length message, 413 message, bad-form message.
ROUTES = {
    "bug-report": ("/api/diagnostics/bug-reports",
                   "Send the report with a Content-Length, not chunked.",
                   "A report needs a Content-Length.",
                   lambda: "The report is too large (text max 256 KB, screenshot max 5 MB).",
                   lambda: "The report form could not be read."),
    "scanlate-pages": ("/api/scanlate/dramas/1/pages",
                       "Send the pages with a Content-Length, not chunked.", UPLOAD_HELP,
                       lambda: "The upload is too large.",
                       lambda: ("Send the pages as multipart/form-data 'files' parts (at most "
                                f"{page_import_limits.MAX_FILES_PER_IMPORT}).")),
    "backup-import-list": ("/api/backups/import/list",
                           "Send the backup file with a Content-Length, not chunked.", UPLOAD_HELP,
                           _backup_too_large, lambda: BACKUP_HELP),
    "backup-import": ("/api/backups/import",
                      "Send the backup file with a Content-Length, not chunked.", UPLOAD_HELP,
                      _backup_too_large, lambda: BACKUP_HELP),
    "drama-cover": ("/api/dramas/1/cover",
                    "Send the image with a Content-Length, not chunked.", UPLOAD_HELP,
                    lambda: "That image is too large (at most 10 MB).",
                    lambda: "Send the image as multipart/form-data in a 'file' field."),
    "subtitle-preview": ("/api/subtitle-import/dramas/1/preview",
                         "Send the file with a Content-Length, not chunked.", UPLOAD_HELP,
                         lambda: "That file is too large (at most 2 MB).",
                         lambda: SUBTITLE_HELP),
    "subtitle-apply": ("/api/subtitle-import/dramas/1/apply",
                       "Send the file with a Content-Length, not chunked.", UPLOAD_HELP,
                       lambda: "That file is too large (at most 2 MB).",
                       lambda: SUBTITLE_HELP),
}


@pytest.fixture
def app(isolated_db, monkeypatch):
    monkeypatch.setattr(bug_report_routes, "_limiter",
                        auth_service.SlidingWindowRateLimiter(1000, 600))
    # Small caps so the streamed-body case stays quick; each route reads
    # its cap per request.
    monkeypatch.setattr(page_import_limits, "MAX_IMPORT_BYTES", MB)
    monkeypatch.setattr(media_upload_service, "max_upload_bytes", lambda: MB)
    assert subtitle_parse.MAX_FILE_BYTES == 2 * MB
    return create_app(ApiSettings())


def _call(app, path, headers, chunks):
    """Runs one POST; returns (status, body text, body bytes the app pulled)."""
    sent, pulled, it = [], [0], iter(chunks)

    async def receive():
        try:
            body = next(it)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        pulled[0] += len(body)
        return {"type": "http.request", "body": body, "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "client": ("127.0.0.1", 5000), "server": ("127.0.0.1", 8600),
             "headers": [(b"host", b"127.0.0.1:8600"), (b"x-baihe-local", b"1"),
                         *[(k.encode(), v.encode()) for k, v in headers.items()]]}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    text = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], text.decode("utf-8", "replace"), pulled[0]


def _assert_message(text, message):
    import json
    assert json.loads(text)["error"]["message"] == message
    assert db.LIBRARY_DIR not in text


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_chunked_body_refused(app, route):
    path, chunked, _missing, _large, _bad = ROUTES[route]
    status, text, pulled = _call(app, path, {
        "content-type": "multipart/form-data; boundary=b", "transfer-encoding": "chunked"},
        [b"x" * 1024])
    assert status == 422
    _assert_message(text, chunked)
    assert pulled == 0


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("length", [None, "", "12abc"])
def test_missing_or_unreadable_content_length_refused(app, route, length):
    path, _chunked, missing, _large, _bad = ROUTES[route]
    headers = {"content-type": "multipart/form-data; boundary=b"}
    if length is not None:
        headers["content-length"] = length
    status, text, pulled = _call(app, path, headers, [b"x" * 1024])
    assert status == 422
    _assert_message(text, missing)
    assert pulled == 0


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_over_cap_content_length_413_before_reading(app, route):
    path, _chunked, _missing, too_large, _bad = ROUTES[route]
    status, text, pulled = _call(app, path, {
        "content-type": "multipart/form-data; boundary=b", "content-length": str(10 ** 12)},
        [b"x" * 1024])
    assert status == 413
    _assert_message(text, too_large())
    assert pulled == 0


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_body_longer_than_its_content_length_cut_off(app, route):
    path, _chunked, _missing, too_large, _bad = ROUTES[route]
    head = (b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"f.bin\"\r\n"
            b"Content-Type: application/octet-stream\r\n\r\n")
    chunks = (head if i == 0 else b"a" * MB for i in range(64))
    status, text, pulled = _call(app, path, {
        "content-type": "multipart/form-data; boundary=b", "content-length": "1000"}, chunks)
    assert status == 413
    _assert_message(text, too_large())
    assert pulled < 63 * MB


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_unreadable_form_gets_the_route_error(app, route):
    path, _chunked, _missing, _large, bad_form = ROUTES[route]
    status, text, _pulled = _call(app, path, {
        "content-type": "multipart/form-data", "content-length": "1"}, [b"x"])
    assert status == 422
    _assert_message(text, bad_form())
