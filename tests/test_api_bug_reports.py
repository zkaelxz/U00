"""Report a problem: services/bug_report_service.py and
api/routers/bug_report_routes.py. Git, setup checks and the log are faked;
no network, no subprocess."""
import json
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.routers import bug_report_routes
from api.server import create_app
from services import auth_service
from services import bug_report_service as svc
from services import diagnostics_gaps_service
from tests.api_helpers import session_headers

KEY = "sk-ant-api03-SECRETSECRETSECRET123456"
GKEY = "AIzaSyA1234567890abcdefghijklmnop"
WIN = r"C:\Users\alexwinuser\AppData\Local\Baihe\library\library.db"
POSIX = "/home/someoneelse/private/library/drama.mp4"
COOKIE = "baihe_session=Zm9vYmFyYmF6cXV4cXV1eGNvcmdlZ3JhdWx0Z2FycGx5"
CSRF = "X-CSRF-Token: abcdefghijklmnopqrstuvwxyz0123456789ABCD"
DIRTY = f"boom {KEY} {GKEY} at {WIN} and {POSIX} {COOKIE} {CSRF}"
BAD = (KEY, GKEY, "alexwinuser", "someoneelse", "AppData", "Zm9vYmFyYmF6",
       "abcdefghijklmnopqrstuvwxyz0123456789ABCD")
REMOTE = "https://baihe.example.com"
LOCAL = {"X-Baihe-Local": "1"}
URL = "/api/diagnostics/bug-reports"


def _assert_clean(text):
    for bad in BAD:
        assert bad not in text, bad
    assert db.LIBRARY_DIR not in text


@pytest.fixture
def fakes(isolated_db, monkeypatch):
    monkeypatch.setattr(svc, "_git_commit", lambda: "abc1234def")
    monkeypatch.setattr(diagnostics_gaps_service, "get_setup_checks", lambda: {
        "python": {"version": "3.11.0", "ok": True}, "ffmpeg": {"found": True, "version": None},
        "js_runtime": {"found": False, "name": None},
        "cuda": {"torch_installed": False, "cuda_available": None},
        "files": {"all_present": True, "missing_top_level": []},
        "library_writable": True})
    monkeypatch.setattr(diagnostics_gaps_service, "get_log_tail",
                        lambda n=50, keyword="": ["INFO fine", f"ERROR {DIRTY}"])
    monkeypatch.setattr(svc, "_CACHE", {})
    monkeypatch.setattr(bug_report_routes, "_limiter",
                        auth_service.SlidingWindowRateLimiter(1000, 600))


@pytest.fixture
def client(fakes):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _report(**over):
    body = {
        "what_happened": f"The Review page went blank. {DIRTY}",
        "expected": "It shows the lines.",
        "route": "/drama/3/review?token=" + KEY,
        "route_history": [{"route": "/library", "at": "12:00:01"},
                          {"route": "/drama/3/review", "at": "12:00:05"}],
        "console": [{"level": "error", "message": f"TypeError: x is undefined {DIRTY}",
                     "at": "12:00:06"}],
        "errors": [{"kind": "unhandledrejection", "message": DIRTY,
                    "source": "http://127.0.0.1:5173/assets/index-AbC123.js:1:99", "at": "12:00:06"}],
        "failed_requests": [{"method": "GET", "path": "/api/review/dramas/3/lines",
                             "status": 500, "code": "internal_error", "at": "12:00:06"}],
        "app_version": "Baihe Studio", "api_version": "0.1", "environment": "development",
        "build_id": "index-AbC123.js", "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/140",
        "viewport": {"width": 390, "height": 844, "dpr": 3}, "mode": "pc",
    }
    body.update(over)
    return body


def _post(c, body=None, files=None, headers=None):
    parts = {"report": (None, json.dumps(body if body is not None else _report())), **(files or {})}
    return c.post(URL, files=parts, headers={**LOCAL, **(headers or {})})


def _png(extra=b"tEXtComment\x00secret-note"):
    def chunk(kind, data):
        return len(data).to_bytes(4, "big") + kind + data + b"\x00\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", b"\x00" * 13)
            + chunk(extra[:4], extra[4:]) + chunk(b"IDAT", b"pixels") + chunk(b"IEND", b""))


def _jpeg():
    exif = b"Exif\x00\x00GPS-secret-place"
    app1 = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    app0 = b"\xff\xe0" + (7).to_bytes(2, "big") + b"JFIF\x00"
    return b"\xff\xd8" + app0 + app1 + b"\xff\xda\x00\x02scan-bytes\xff\xd9"


def _folder(report_id):
    return os.path.join(svc._reports_dir(), svc._folders()[report_id])


def test_create_list_get_delete(client):
    r = _post(client, files={"screenshot": ("s.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["id"] == 1 and set(b) == {"id", "stamp", "markdown", "issue_markdown",
                                        "what_happened", "expected", "title"}
    md = b["markdown"]
    # The GitHub-link fields: scrubbed, and never the server section.
    for field in ("issue_markdown", "what_happened", "title"):
        _assert_clean(b[field])
    assert b["title"].startswith("[Bug] The Review page went blank.")
    assert "INFO fine" not in b["issue_markdown"] and "abc1234def" not in b["issue_markdown"]
    assert "Server details are saved with the report on the PC." in b["issue_markdown"]
    assert "The Review page went blank." in md and "It shows the lines." in md
    assert "`/drama/3/review`" in md                    # route kept, query dropped
    assert "| GET | `/api/review/dramas/3/lines` | 500 | internal_error |" in md
    assert "abc1234def" in md and "Python 3.11.0 (ok)" in md and "INFO fine" in md
    assert "saved with the report on the PC" in md
    _assert_clean(r.text)

    folder = _folder(1)
    assert sorted(os.listdir(folder)) == ["report.json", "report.md", "screenshot.png"]
    with open(os.path.join(folder, "screenshot.png"), "rb") as fh:
        shot = fh.read()
    assert b"tEXt" not in shot and b"secret-note" not in shot and b"IDAT" in shot

    assert _post(client, _report(include_server_log=False)).json()["id"] == 2
    listed = client.get(URL)
    assert listed.status_code == 200
    items = listed.json()
    assert [i["id"] for i in items] == [2, 1]
    assert items[1]["has_screenshot"] is True and items[1]["has_server_log"] is True
    assert items[0]["has_server_log"] is False and items[0]["route"] == "/drama/3/review"
    _assert_clean(listed.text)
    assert "bug_reports" not in listed.text

    one = client.get(f"{URL}/1")
    assert one.status_code == 200 and one.json()["markdown"] == md
    assert one.json()["stamp"] == b["stamp"] == items[1]["stamp"]
    assert client.get(f"{URL}/99").status_code == 404

    stamp = b["stamp"]
    assert client.post(f"{URL}/1/delete", json={"confirm": False, "stamp": stamp}).status_code == 422
    assert client.post(f"{URL}/1/delete", json={"confirm": True}).status_code == 422
    assert client.post(f"{URL}/1/delete",
                       json={"confirm": True, "stamp": "20000101T000000Z"}).status_code == 404
    d = client.post(f"{URL}/1/delete", json={"confirm": True, "stamp": stamp})
    assert d.status_code == 200 and d.json() == {"id": 1, "deleted": True}
    assert not os.path.exists(folder)
    assert client.post(f"{URL}/1/delete", json={"confirm": True, "stamp": stamp}).status_code == 404
    assert client.get(f"{URL}/1").status_code == 404


def test_planted_key_and_windows_path_redacted_on_disk(client):
    r = _post(client)
    assert r.status_code == 200
    folder = _folder(r.json()["id"])
    for name in ("report.json", "report.md"):
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            text = fh.read()
        _assert_clean(text)
        assert "[REDACTED]" in text and ".../library.db" in text
    with open(os.path.join(folder, "report.json"), encoding="utf-8") as fh:
        stored = json.load(fh)
    assert stored["server"]["log_tail"][0] == "INFO fine"
    assert stored["client"]["failed_requests"][0]["path"] == "/api/review/dramas/3/lines"


def test_nul_and_forged_url_placeholders_do_not_fail_the_report(client):
    """NUL marks redaction's URL placeholders; a forged one used to raise."""
    for text in ("x \x00URL7\x00", "\x00URL" + "9" * 4400 + "\x00 https://h/p?sig=1"):
        r = _post(client, _report(what_happened=text))
        assert r.status_code == 200, r.text
        assert "\\u0000" not in json.dumps(svc.get_report(r.json()["id"]))


def test_jpeg_metadata_stripped_and_bad_images_refused(client):
    r = _post(client, files={"screenshot": ("s.jpg", _jpeg(), "image/jpeg")})
    assert r.status_code == 200
    with open(os.path.join(_folder(r.json()["id"]), "screenshot.jpg"), "rb") as fh:
        shot = fh.read()
    assert b"GPS-secret-place" not in shot and b"JFIF" in shot and b"scan-bytes" in shot
    gif = _post(client, files={"screenshot": ("s.gif", b"GIF89a....", "image/gif")})
    assert gif.status_code == 422
    big = _post(client, files={"screenshot": ("s.png", b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024),
                                              "image/png")})
    assert big.status_code == 422
    assert [i["id"] for i in client.get(URL).json()] == [1]


def test_validation_422(client):
    assert _post(client, _report(what_happened="")).status_code == 422
    assert _post(client, _report(mode="moon")).status_code == 422
    assert _post(client, _report(console=[{"level": "error", "message": "x"}] * 31)).status_code == 422
    assert _post(client, _report(headers={"Cookie": "x"})).status_code == 422   # extra forbidden
    r = client.post(URL, files={"report": (None, "{not json")}, headers=LOCAL)
    assert r.status_code == 422
    r = client.post(URL, files={"report": (None, "x" * (svc.MAX_JSON_BYTES + 10))}, headers=LOCAL)
    assert r.status_code == 422
    r = client.post(URL, json=_report(), headers=LOCAL)
    assert r.status_code == 422
    assert client.get(URL).json() == []
    assert client.get(f"{URL}/0").status_code == 422


def test_cap_409(fakes, monkeypatch):
    monkeypatch.setattr(svc, "MAX_REPORTS", 2)
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    kids = [_session(auth_service.add_user(f"kid{i}@example.com")) for i in range(3)]
    assert _post(remote, headers=session_headers(kids[0])).status_code == 200
    assert _post(remote, headers=session_headers(kids[1])).status_code == 200
    r = _post(remote, headers=session_headers(kids[2]))
    assert r.status_code == 409
    _assert_clean(r.text)
    # The shared cap counts remote reports only: the PC owner can still report.
    assert _post(_local_on(app), headers=session_headers(kids[2])).status_code == 200


def test_per_user_cap_leaves_others_able_to_report(fakes, monkeypatch):
    monkeypatch.setattr(svc, "MAX_REPORTS_PER_USER", 2)
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    noisy = _session(auth_service.add_user("noisy@example.com"))
    other = _session(auth_service.add_user("other@example.com"))
    assert _post(remote, headers=session_headers(noisy)).status_code == 200
    assert _post(remote, headers=session_headers(noisy)).status_code == 200
    r = _post(remote, headers=session_headers(noisy))
    assert r.status_code == 409 and "PC owner" in r.json()["error"]["message"]
    assert _post(remote, headers=session_headers(other)).status_code == 200
    # Deleting one of theirs frees a place for the noisy user again.
    item = next(i for i in svc.list_reports() if i["id"] == 1)
    svc.delete_report(1, stamp=item["stamp"], confirm=True)
    assert _post(remote, headers=session_headers(noisy)).status_code == 200


def test_pc_reports_do_not_count_against_remote_caps(fakes, monkeypatch):
    monkeypatch.setattr(svc, "MAX_REPORTS", 1)
    monkeypatch.setattr(svc, "MAX_REPORTS_PER_USER", 1)
    for _ in range(3):
        svc.create_report(_report())
    assert svc.create_report(_report(), reporter="user:7")["id"] == 4
    with pytest.raises(svc.ConflictError):
        svc.create_report(_report(), reporter="user:8")
    assert svc.create_report(_report())["id"] == 5


def test_pc_report_refused_over_the_total_size_cap(fakes, monkeypatch):
    # MAX_TOTAL_BYTES is the only bound on the PC owner's reports.
    svc.create_report(_report())
    monkeypatch.setattr(svc, "MAX_TOTAL_BYTES", 1)
    with pytest.raises(svc.ConflictError):
        svc.create_report(_report())
    assert [i["id"] for i in svc.list_reports()] == [1]


def test_markdown_fence_survives_backticks(fakes):
    out = svc.create_report(_report(console=[{"level": "warn", "message": "a ```` b"}]))
    assert "`````text" in out["markdown"]


def _session(user):
    return auth_service.create_session(user["id"])



def test_auth_on(fakes):
    app = create_app(ApiSettings(auth_mode="on"))
    c = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    assert _post(c).status_code == 401
    assert c.get(URL).status_code == 401
    assert c.get(f"{URL}/1").status_code == 401

    nobody = auth_service.add_user("nobody@example.com")
    auth_service.revoke_permission(nobody["id"], "library.read")
    assert _post(c, headers=session_headers(_session(nobody))).status_code == 403
    kid = _session(auth_service.add_user("kid@example.com"))   # household defaults
    # CSRF is required for the POST.
    assert _post(c, headers={"Cookie": session_headers(kid)["Cookie"]}).status_code == 403
    r = _post(c, headers=session_headers(kid))
    assert r.status_code == 200, r.text
    md = r.json()["markdown"]
    assert "Server details are saved with the report on the PC." in md
    assert "INFO fine" not in md and "abc1234def" not in md
    _assert_clean(r.text)
    assert c.get(URL, headers=session_headers(kid)).status_code == 403
    assert c.get(f"{URL}/1", headers=session_headers(kid)).status_code == 403

    admin = _session(auth_service.grant_admin_local("admin@example.com"))
    r = _post(c, headers=session_headers(admin))
    assert r.status_code == 200 and "INFO fine" in r.json()["markdown"]
    assert [i["id"] for i in c.get(URL, headers=session_headers(admin)).json()] == [2, 1]
    full = c.get(f"{URL}/1", headers=session_headers(admin))
    assert full.status_code == 200 and "INFO fine" in full.json()["markdown"]

    # Delete is PC only: refused remotely even for the admin, allowed at the PC.
    stamp = c.get(URL, headers=session_headers(admin)).json()[1]["stamp"]
    body = {"confirm": True, "stamp": stamp}
    assert c.post(f"{URL}/1/delete", json=body, headers=session_headers(admin)).status_code == 403
    local = TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
    assert local.post(f"{URL}/1/delete", json=body,
                      headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert local.post(f"{URL}/1/delete", json=body).status_code == 200


def test_auth_off_multipart_needs_local_header(client):
    r = client.post(URL, files={"report": (None, json.dumps(_report()))})
    assert r.status_code == 403
    assert _post(client).status_code == 200


# --- security-review fixes ----------------------------------------------------

def _local_on(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def test_screenshot_is_pc_only_with_auth_on(fakes):
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    kid = _session(auth_service.add_user("kid@example.com"))
    shot = {"screenshot": ("s.png", _png(), "image/png")}
    r = _post(remote, files=shot, headers=session_headers(kid))
    assert r.status_code == 403
    assert "only be attached at the main PC" in r.json()["error"]["message"]
    assert not os.path.isdir(svc._reports_dir()) or svc._folders() == {}
    # The same file sent as a plain field is refused too.
    r = remote.post(URL, files={"report": (None, json.dumps(_report())), "screenshot": (None, "x")},
                    headers={**LOCAL, **session_headers(kid)})
    assert r.status_code in (403, 422) and svc._folders() == {}
    ok = _post(remote, headers=session_headers(kid))                     # text only: fine remotely
    assert ok.status_code == 200, ok.text
    assert _post(_local_on(app), files=shot, headers=session_headers(kid)).status_code == 200   # at the PC
    assert [i["has_screenshot"] for i in svc.list_reports()] == [True, False]


def test_transfer_encoding_refused(client):
    body = b"--b\r\nContent-Disposition: form-data; name=\"report\"\r\n\r\n{}\r\n--b--\r\n"
    r = client.post(URL, content=body, headers={
        **LOCAL, "Content-Type": "multipart/form-data; boundary=b",
        "Content-Length": str(len(body)), "Transfer-Encoding": "chunked"})
    assert r.status_code == 422 and "chunked" in r.json()["error"]["message"]

    def chunks():
        for _ in range(8):
            yield b"x" * (1024 * 1024)
    r = client.post(URL, content=chunks(), headers={
        **LOCAL, "Content-Type": "multipart/form-data; boundary=b"})
    assert r.status_code == 422
    assert svc._folders() == {}


def test_body_counted_whatever_the_headers_say(fakes):
    """A Content-Length under the cap but a longer body (what h11 lets
    through for CL+TE) is cut off with 413 by the counting receive."""
    import asyncio
    app = create_app(ApiSettings())
    chunk = (b"--b\r\nContent-Disposition: form-data; name=\"screenshot\"; filename=\"s.png\"\r\n"
             b"Content-Type: image/png\r\n\r\n" + b"a" * 1024)
    sent, total = [], 0

    async def receive():
        nonlocal total
        total += 1024 * 1024
        more = total < bug_report_routes._MAX_BODY + 4 * 1024 * 1024
        body = chunk if total == 1024 * 1024 else b"a" * (1024 * 1024)
        return {"type": "http.request", "body": body, "more_body": more}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": URL, "raw_path": URL.encode(), "query_string": b"",
             "root_path": "", "client": ("127.0.0.1", 5000), "server": ("127.0.0.1", 8600),
             "headers": [(b"host", b"127.0.0.1:8600"), (b"x-baihe-local", b"1"),
                         (b"content-type", b"multipart/form-data; boundary=b"),
                         (b"content-length", b"1000")]}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413
    assert total <= bug_report_routes._MAX_BODY + 2 * 1024 * 1024
    assert svc._folders() == {}


def test_over_cap_content_length_413(client):
    r = client.post(URL, content=b"x", headers={
        **LOCAL, "Content-Type": "multipart/form-data; boundary=b",
        "Content-Length": str(bug_report_routes._MAX_BODY + 1)})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"


def test_deeply_nested_json_422(client):
    deep = "[" * 100000 + "]" * 100000
    r = client.post(URL, files={"report": (None, deep)}, headers=LOCAL)
    assert r.status_code == 422


def test_rate_limit_per_principal(client, monkeypatch):
    monkeypatch.setattr(bug_report_routes, "_limiter", auth_service.SlidingWindowRateLimiter(2, 600))
    assert _post(client).status_code == 200
    assert _post(client).status_code == 200
    r = _post(client)
    assert r.status_code == 429
    assert [i["id"] for i in client.get(URL).json()] == [2, 1]


def test_cap_checked_before_server_work(fakes, monkeypatch):
    monkeypatch.setattr(svc, "MAX_REPORTS", 1)
    svc.create_report(_report(), reporter="user:1")
    calls = []
    monkeypatch.setattr(svc, "_log_tail", lambda: calls.append("log") or [])
    monkeypatch.setattr(svc, "_git_commit", lambda: calls.append("git"))
    monkeypatch.setattr(svc, "_setup_summary", lambda: calls.append("setup") or "")
    monkeypatch.setattr(svc, "_CACHE", {})
    with pytest.raises(svc.ConflictError):
        svc.create_report(_report(), reporter="user:2")
    assert calls == []


def test_git_and_setup_cached(fakes, monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "_git_commit", lambda: calls.append("git") or "abc1234def")
    monkeypatch.setattr(svc, "_setup_summary", lambda: calls.append("setup") or "ok")
    svc.create_report(_report())
    svc.create_report(_report())
    assert calls == ["git", "setup"]


def test_ids_never_reused(fakes):
    first = svc.create_report(_report())
    second = svc.create_report(_report())
    svc.delete_report(second["id"], stamp=second["stamp"], confirm=True)
    third = svc.create_report(_report())
    assert (first["id"], second["id"], third["id"]) == (1, 2, 3)
    svc.delete_report(first["id"], stamp=first["stamp"], confirm=True)
    svc.delete_report(third["id"], stamp=third["stamp"], confirm=True)
    assert svc.create_report(_report())["id"] == 4


def test_pre_scrub_urls_params_and_cookies(fakes):
    text = svc._scrub(
        "GET https://alice:hunter2pass@cdn.example.com/a.png?X-Amz-Signature=abc&Key-Pair-Id=K1 "
        "then /x?sig=s1&signature=s2&auth=a1 token=t1 access_token=t2\n"
        "Cookie: theme=dark; sid=abcdef\nSet-Cookie: other=zzz; Path=/\n"
        '{"cookie": "c=1"}')
    for bad in ("alice", "hunter2pass", "=abc", "K1", "s1", "s2", "a1", "t1", "t2",
                "theme=dark", "abcdef", "other=zzz", "c=1"):
        assert bad not in text, (bad, text)
    assert "//***@cdn.example.com" in svc._pre_scrub("https://alice:pw@cdn.example.com/x")


def _page_payload(**over):
    import base64
    body = {"image": "data:image/jpeg;base64," + base64.b64encode(_jpeg()).decode(),
            "note": "box covers the face", "chapter": "Chapter 3",
            "page_url": "https://example.com/ch3?token=abc", "image_url": "https://cdn.example.com/3/05.jpg",
            "width": 800, "height": 1200, "notes": ["one box was left alone"],
            "regions": [{"x": 1, "y": 2, "w": 30, "h": 40, "source_text": "你好", "translated_text": "Hello",
                         "kind": "bubble", "reading_order": 0, "untranslated": False, "left_alone": False}]}
    body.update(over)
    return body


def test_page_report_saves_picture_regions_and_note(fakes):
    saved = svc.create_page_report(_page_payload())
    folder = _folder(saved["id"])
    assert sorted(os.listdir(folder)) == ["page.json", "report.json", "report.md", "screenshot.jpg"]
    page = json.load(open(os.path.join(folder, "page.json"), encoding="utf-8"))
    assert page["regions"][0]["source_text"] == "你好"
    assert page["page_url"] == "https://example.com/ch3"
    markdown = svc.get_report(saved["id"])["markdown"]
    assert "box covers the face" in markdown and "| 1 | 你好 | Hello |" in markdown
    assert "GPS-secret-place" not in open(os.path.join(folder, "screenshot.jpg"), "rb").read().decode("latin-1")
    assert svc.list_reports()[0]["summary"].startswith("Quick read page report")


def test_page_report_refuses_a_missing_or_oversized_picture(fakes):
    from lib.errors import InvalidInputError
    for bad in ({"image": ""}, {"image": "data:image/jpeg;base64,!!!"},
                {"image": "data:image/png;base64,AAAA"}):
        with pytest.raises(InvalidInputError):
            svc.create_page_report(_page_payload(**bad))
    import base64
    big = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"0" * svc.MAX_PAGE_IMAGE_BYTES).decode()
    with pytest.raises(InvalidInputError):
        svc.create_page_report(_page_payload(image=big))


def test_page_reports_count_against_a_members_cap(fakes):
    from lib.errors import ConflictError
    for _ in range(svc.MAX_REPORTS_PER_USER):
        svc.create_page_report(_page_payload(), reporter="user:7")
    with pytest.raises(ConflictError):
        svc.create_page_report(_page_payload(), reporter="user:7")
    svc.create_page_report(_page_payload(), reporter="user:8")


def test_page_report_addresses_lose_signed_paths_and_markdown_breakers(fakes):
    saved = svc.create_page_report(_page_payload(
        image_url="https://cdn.example.com/priv/AbCdEfGhIjKlMnOpQrStUv123456/05.jpg",
        page_url="https://example.com/ch3#k=secret", chapter="Ch`3\r| x",
        regions=[{"source_text": "a\r|b`c", "translated_text": "d"}]))
    text = svc.get_report(saved["id"])["markdown"]
    assert "AbCdEfGh" not in text and "k=secret" not in text and "\r" not in text
    assert "`c" not in text.split("## Quick read page", 1)[1].split("Text regions")[1]


def test_page_report_refuses_a_png_sent_as_a_jpeg(fakes):
    import base64
    from lib.errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        svc.create_page_report(_page_payload(image="data:image/jpeg;base64," + base64.b64encode(_png()).decode()))


def test_remote_page_reports_stop_before_the_owners_room_runs_out(fakes, monkeypatch):
    from lib.errors import ConflictError
    monkeypatch.setattr(svc, "MAX_REMOTE_TOTAL_BYTES", 1)
    svc.create_page_report(_page_payload())          # the PC owner's own report still saves
    with pytest.raises(ConflictError):
        svc.create_page_report(_page_payload(), reporter="user:7")


def test_ips_stripped_and_browser_versions_kept(client):
    body = _report(what_happened="Cannot reach 192.168.1.50 or 10.0.0.7:8600 today",
                   page_title="Sample Title",
                   user_agent="Mozilla/5.0 (Linux; Android 14) Chrome/120.0.6099.43 Mobile")
    saved = _post(client, body).json()
    md = saved["markdown"]
    assert "192.168.1.50" not in md and "10.0.0.7" not in md and "[IP]" in md
    # The page title can name the work being read, so the public issue text leaves it out.
    assert "Sample Title" not in saved["issue_markdown"]
    assert "120.0.6099.43" in md
    v6 = svc._scrub("seen fe80::1 and 2001:db8:1:2:3:4:5:6 at 12:00:01, ends 10.0.0.7.")
    assert "fe80" not in v6 and "2001" not in v6 and "10.0.0.7" not in v6 and "12:00:01" in v6
    assert "(Sample Title)" in md and "At a glance: #1" in md


def test_phone_build_and_crash_text_are_kept_and_scrubbed(client):
    md = _post(client, _report(phone_info="Build 12, Android SDK 34",
                               phone_crash="java.lang.IllegalStateException at 10.0.0.7 " + KEY)).json()["markdown"]
    assert "Build 12" in md and "IllegalStateException" in md and "10.0.0.7" not in md and KEY not in md


def test_server_section_lists_recent_jobs_and_bundled_extension(client, monkeypatch):
    import background_jobs
    monkeypatch.setattr(background_jobs, "list_all_jobs", lambda: {
        "job-old": {"status": "done", "started_at": 1},
        "job-bad": {"status": "error", "started_at": 2, "description": "Translate",
                    "error": f"boom {KEY} on 192.168.1.9"}})
    md = _post(client).json()["markdown"]
    assert md.index("job-bad") < md.index("job-old")
    assert "Translate" in md and "boom" in md
    assert KEY not in md and "192.168.1.9" not in md
    assert "Extension bundled with this app:" in md


def test_a_report_from_away_tells_the_owner_and_one_from_the_pc_does_not(fakes, monkeypatch):
    sent = []
    from services import notification_service
    monkeypatch.setattr(notification_service, "notify_waiting",
                        lambda text, **kw: sent.append((text, kw)))
    svc.create_report(_report())
    assert sent == []
    saved = svc.create_report(_report(), reporter="user:7")
    assert len(sent) == 1
    text, kw = sent[0]
    assert f"#{saved['id']}" in text and "Diagnostics at the PC" in text
    assert kw.get("owner_user_id") is None and "user:7" not in text
