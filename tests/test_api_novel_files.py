"""Tests for /api/novel/dramas/{id}/reference and /raw-novel (parity audit
B1 #3/#4): the English novel reference and the raw original-language
novel. Isolated library, tiny in-test files, no network or models."""

import io
import json
import os
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import novel_files_service as svc
from services import translate_run_service

LOCAL = {"X-Baihe-Local": "1"}
REMOTE = "https://baihe.example.com"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False, headers=LOCAL)


def _drama(**kw):
    return db.create_drama(title_en="D", **kw)


def _ref(did):
    return f"/api/novel/dramas/{did}/reference"


def _raw(did):
    return f"/api/novel/dramas/{did}/raw-novel"


def _up(c, url, data, name="book.txt", **kw):
    return c.post(url, files={"file": (name, data, "text/plain")}, **kw)


def _file(did, name):
    with open(os.path.join(db.DRAMAS_DIR, str(did), name), encoding="utf-8") as f:
        return f.read()


def _no_leak(body):
    text = json.dumps(body)
    for needle in (db.DRAMAS_DIR, "novel_reference.txt", "raw_novel_context.txt",
                   "book.txt", "/", "\\\\"):
        assert needle not in text, needle


def _epub(body_html):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("OEBPS/a.xhtml", f"<html><body>{body_html}</body></html>")
    return buf.getvalue()


# --- reference ---------------------------------------------------------------

def test_reference_upload_status_replace_remove(client):
    did = _drama()
    s = client.get(_ref(did))
    assert s.status_code == 200
    assert s.json() == {"drama_id": did, "present": False, "size_bytes": 0, "char_count": 0}

    r = _up(client, _ref(did), "Chapter 1\r\nShen Qingyi drew her sword.".encode())
    assert r.status_code == 200, r.text
    body = r.json()
    _no_leak(body)
    assert body["present"] and not body["replaced"]
    assert body["char_count"] == len("Chapter 1\nShen Qingyi drew her sword.")
    assert db.get_drama(did)["novel_reference_filename"] == "novel_reference.txt"
    assert _file(did, "novel_reference.txt") == "Chapter 1\nShen Qingyi drew her sword."
    # the translate run reads exactly what was uploaded
    assert translate_run_service.load_novel_reference(did, db.get_drama(did)).startswith("Chapter 1")
    assert translate_run_service.get_translate_config(did)["has_novel_reference"] is True

    r = _up(client, _ref(did), b"New text", name="n.md")
    assert r.status_code == 200 and r.json()["replaced"] is True
    assert _file(did, "novel_reference.txt") == "New text"
    st = client.get(_ref(did)).json()
    assert st == {"drama_id": did, "present": True, "size_bytes": 8, "char_count": 8}

    assert client.post(_ref(did) + "/remove", json={}).status_code == 422
    r = client.post(_ref(did) + "/remove", json={"confirm": True})
    assert r.status_code == 200 and r.json() == {"drama_id": did, "removed": True, "present": False}
    assert db.get_drama(did)["novel_reference_filename"] is None
    assert not os.path.exists(os.path.join(db.DRAMAS_DIR, str(did), "novel_reference.txt"))
    assert client.post(_ref(did) + "/remove", json={"confirm": True}).status_code == 404


def test_reference_encoding_fallback_cp1252(client):
    did = _drama()
    r = _up(client, _ref(did), "café — done".encode("cp1252"))
    assert r.status_code == 200
    assert _file(did, "novel_reference.txt") == "café — done"


def test_reference_errors(client, monkeypatch):
    assert client.get(_ref(999)).status_code == 404
    assert _up(client, _ref(999), b"x").status_code == 404
    assert client.post(_ref(999) + "/remove", json={"confirm": True}).status_code == 404
    did = _drama()
    assert _up(client, _ref(did), b"x", name="book.epub").status_code == 422
    assert _up(client, _ref(did), b"x", name="book.exe").status_code == 422
    assert _up(client, _ref(did), b"").status_code == 422
    assert _up(client, _ref(did), b"  \r\n ").status_code == 422
    monkeypatch.setattr(svc, "MAX_TEXT_BYTES", 10)
    r = _up(client, _ref(did), b"x" * 11)
    assert r.status_code == 422
    _no_leak(r.json())
    assert db.get_drama(did)["novel_reference_filename"] is None


def test_reference_stored_name_outside_folder_is_ignored(client):
    did = _drama()
    db.update_drama(did, novel_reference_filename="../../etc/passwd")
    assert client.get(_ref(did)).json()["present"] is False
    # removal clears the bad field without touching anything outside
    r = client.post(_ref(did) + "/remove", json={"confirm": True})
    assert r.status_code == 200
    assert db.get_drama(did)["novel_reference_filename"] is None


def test_upload_refused_while_job_runs(client, monkeypatch):
    did = _drama()
    _up(client, _ref(did), b"old ref")
    _up(client, _raw(did), "旧".encode())
    monkeypatch.setattr(background_jobs, "any_job_running_for_drama", lambda d: d == did)
    assert _up(client, _ref(did), b"new ref").status_code == 409
    assert _up(client, _raw(did), "新".encode()).status_code == 409
    assert client.post(_ref(did) + "/remove", json={"confirm": True}).status_code == 409
    assert _file(did, "novel_reference.txt") == "old ref"
    assert _file(did, "raw_novel_context.txt") == "旧"
    # another drama's job does not block
    other = _drama()
    assert _up(client, _ref(other), b"fine").status_code == 200


# --- raw novel ---------------------------------------------------------------

def test_raw_novel_upload_status_and_existing_remove(client):
    did = _drama()
    assert client.get(_raw(did)).json()["present"] is False
    r = _up(client, _raw(did), "沈清疑拔剑。".encode("utf-8"))
    assert r.status_code == 200, r.text
    _no_leak(r.json())
    assert r.json()["char_count"] == 6 and r.json()["size_bytes"] == 18
    assert _file(did, "raw_novel_context.txt") == "沈清疑拔剑。"
    st = client.get(_raw(did)).json()
    assert st["present"] and st["char_count"] == 6
    # the existing delete route removes what this uploaded
    rm = client.post(f"/api/novel/dramas/{did}/raw-novel/remove", json={"confirm": True})
    assert rm.status_code == 200
    assert client.get(_raw(did)).json()["present"] is False


def test_raw_novel_legacy_encodings(client):
    did = _drama()
    _up(client, _raw(did), "沈清疑拔剑。".encode("gb18030"))
    assert _file(did, "raw_novel_context.txt") == "沈清疑拔剑。"
    tw = _drama(chinese_script="traditional")
    _up(client, _raw(tw), "雲隱宗".encode("big5"))
    assert _file(tw, "raw_novel_context.txt") == "雲隱宗"
    ja = _drama(source_language="ja")
    _up(client, _raw(ja), "こんにちは".encode("cp932"))
    assert _file(ja, "raw_novel_context.txt") == "こんにちは"
    bom = _drama()
    _up(client, _raw(bom), "﻿甲".encode("utf-8"))
    assert _file(bom, "raw_novel_context.txt") == "甲"


def test_raw_novel_epub_and_errors(client):
    did = _drama()
    r = _up(client, _raw(did), _epub("<p>第一章</p>"), name="b.epub")
    assert r.status_code == 200 and r.json()["char_count"] == 3
    assert _file(did, "raw_novel_context.txt") == "第一章"
    assert _up(client, _raw(did), b"not a zip", name="b.epub").status_code == 422
    assert _up(client, _raw(did), b"x", name="b.pdf").status_code == 422
    assert _up(client, _raw(999), b"x").status_code == 404
    assert client.get(_raw(999)).status_code == 404
    # failed uploads left the saved text alone
    assert _file(did, "raw_novel_context.txt") == "第一章"


def test_raw_novel_feeds_auto_prompt(client):
    from services import transcribe_service
    did = _drama()
    _up(client, _raw(did), "云隐宗的弟子沈清疑。".encode())
    prompt = transcribe_service.build_auto_initial_prompt(did)
    assert "云隐宗" in prompt


def test_multipart_upload_without_local_header_refused(isolated_db):
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    did = _drama()
    assert _up(c, _ref(did), b"x").status_code == 403
    assert _up(c, _raw(did), b"x").status_code == 403


# --- permissions (BAIHE_API_AUTH=on) -----------------------------------------

def _user(email, *perms):
    u = auth_service.add_user(email)
    for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
        if p not in perms:
            auth_service.revoke_permission(u["id"], p)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    return auth_service.create_session(u["id"], "pytest", "203.0.113.9")


def _h(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_status_needs_library_read(isolated_db):
    remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                        raise_server_exceptions=False)
    did = _drama()
    nobody = _user("n@example.com")
    reader = _user("r@example.com", "library.read")
    for url in (_ref(did), _raw(did)):
        assert remote.get(url).status_code == 401
        assert remote.get(url, headers=_h(nobody)).status_code == 403
        r = remote.get(url, headers=_h(reader))
        assert r.status_code == 200 and r.json()["present"] is False


def test_writes_are_pc_only(isolated_db):
    did = _drama()
    remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                        raise_server_exceptions=False)
    admin = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(admin["id"], "pytest", "203.0.113.9")
    for h in ({}, {**_h(s), **LOCAL}):
        assert _up(remote, _ref(did), b"x", headers=h).status_code in (401, 403)
        assert _up(remote, _raw(did), b"x", headers=h).status_code in (401, 403)
        assert remote.post(_ref(did) + "/remove", json={"confirm": True},
                           headers=h).status_code in (401, 403)
    assert db.get_drama(did)["novel_reference_filename"] is None
    local = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="http://127.0.0.1:8600",
                       client=("127.0.0.1", 5000), raise_server_exceptions=False, headers=LOCAL)
    assert _up(local, _ref(did), b"ok").status_code == 200
    assert _up(local, _raw(did), b"ok").status_code == 200
    assert local.post(_ref(did) + "/remove", json={"confirm": True}).status_code == 200
    proxied = _up(local, _ref(did), b"x", headers={"X-Forwarded-For": "1.2.3.4"})
    assert proxied.status_code == 403


# --- pasted text (reference and raw novel) -----------------------------------

def test_reference_paste_sets_and_replaces(client):
    did = _drama()
    r = client.post(_ref(did) + "/text", json={"text": "  Pasted\r\nreference  "})
    assert r.status_code == 200, r.text
    _no_leak(r.json())
    assert r.json()["replaced"] is False and r.json()["char_count"] == len("Pasted\nreference")
    assert _file(did, "novel_reference.txt") == "Pasted\nreference"
    assert db.get_drama(did)["novel_reference_filename"] == "novel_reference.txt"
    r = client.post(_ref(did) + "/text", json={"text": "Second"})
    assert r.json()["replaced"] is True
    assert client.get(_ref(did)).json()["char_count"] == 6


def test_raw_novel_paste(client):
    did = _drama()
    r = client.post(_raw(did) + "/text", json={"text": "云隐宗"})
    assert r.status_code == 200 and r.json()["char_count"] == 3
    assert _file(did, "raw_novel_context.txt") == "云隐宗"


def test_paste_errors(client, monkeypatch):
    did = _drama()
    for url in (_ref(did) + "/text", _raw(did) + "/text"):
        assert client.post(url, json={"text": ""}).status_code == 422
        assert client.post(url, json={"text": " \n\t "}).status_code == 422
        assert client.post(url, json={"text": 5}).status_code == 422
        assert client.post(url, json={"text": "x", "extra": 1}).status_code == 422
        assert client.post(url, json={}).status_code == 422
        bad = client.post(url, content=b"not json secret-ish",
                          headers={"Content-Type": "application/json"})
        assert bad.status_code == 422 and "secret-ish" not in bad.text
    assert client.post(_ref(999) + "/text", json={"text": "x"}).status_code == 404
    assert client.post(_raw(999) + "/text", json={"text": "x"}).status_code == 404
    monkeypatch.setattr(svc, "MAX_TEXT_CHARS", 5)
    assert client.post(_ref(did) + "/text", json={"text": "123456"}).status_code == 422
    monkeypatch.setattr(svc, "MAX_TEXT_BYTES", 20)
    r = client.post(_raw(did) + "/text", json={"text": "x" * 30})
    assert r.status_code == 422 and "too large" in r.json()["error"]["message"]
    assert db.get_drama(did)["novel_reference_filename"] is None
    assert not os.path.exists(os.path.join(db.DRAMAS_DIR, str(did), "raw_novel_context.txt"))


def test_paste_refused_while_job_runs(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(background_jobs, "any_job_running_for_drama", lambda d: d == did)
    assert client.post(_ref(did) + "/text", json={"text": "x"}).status_code == 409
    assert client.post(_raw(did) + "/text", json={"text": "x"}).status_code == 409


def test_raw_novel_writes_refused_during_any_source_import(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {"source_import_demo_42": {"status": "running"}})
    assert _up(client, _raw(did), b"x").status_code == 409
    assert client.post(_raw(did) + "/text", json={"text": "x"}).status_code == 409
    # an import never writes the reference, so that stays allowed
    assert client.post(_ref(did) + "/text", json={"text": "x"}).status_code == 200
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {"source_import_demo_42": {"status": "done"}})
    assert client.post(_raw(did) + "/text", json={"text": "x"}).status_code == 200


def test_source_import_in_another_process_blocks_raw_novel(client):
    did = _drama()
    db.save_job_record("source_import_demo_7", "running")
    assert client.post(_raw(did) + "/text", json={"text": "x"}).status_code == 409
    db.save_job_record("source_import_demo_7", "done")
    assert client.post(_raw(did) + "/text", json={"text": "x"}).status_code == 200


def test_paste_routes_are_pc_only(isolated_db):
    did = _drama()
    remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                        raise_server_exceptions=False)
    admin = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(admin["id"], "pytest", "203.0.113.9")
    for url in (_ref(did) + "/text", _raw(did) + "/text"):
        assert remote.post(url, json={"text": "x"}).status_code in (401, 403)
        assert remote.post(url, json={"text": "x"}, headers=_h(s)).status_code == 403
    # a text/plain "simple" POST (another local page, no preflight) is refused
    off = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = off.post(_ref(did) + "/text", content=b'{"text": "x"}',
                 headers={"Content-Type": "text/plain"})
    assert r.status_code == 403
    assert db.get_drama(did)["novel_reference_filename"] is None


# --- corrupt EPUB entries (security review LOW-3) ----------------------------

def _corrupt_epub():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("OEBPS/a.xhtml", "<html><body><p>" + "第一章 " * 400 + "</p></body></html>")
    data = bytearray(buf.getvalue())
    info = zipfile.ZipFile(io.BytesIO(bytes(data))).infolist()[0]
    start = info.header_offset + 30 + len(info.filename.encode()) + 10
    for i in range(start, start + 40):
        data[i] ^= 0xFF
    return bytes(data)


def test_corrupt_epub_entry_is_422_not_500(client):
    did = _drama()
    r = _up(client, _raw(did), _corrupt_epub(), name="b.epub")
    assert r.status_code == 422, r.text
    assert "not a valid EPUB" in r.json()["error"]["message"]
    r = client.post(f"/api/novel/dramas/{did}/attach-epub",
                    files={"file": ("b.epub", _corrupt_epub(), "application/epub+zip")})
    assert r.status_code == 422, r.text


def _corrupt_lzma_epub():
    # an LZMA (method 14) entry whose compressed data is garbled: zipfile
    # raises lzma.LZMAError ("Corrupt input data"), which is not an OSError
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_LZMA) as z:
        z.writestr("OEBPS/a.xhtml", "<html><body><p>" + "第一章 " * 400 + "</p></body></html>")
    data = bytearray(buf.getvalue())
    info = zipfile.ZipFile(io.BytesIO(bytes(data))).infolist()[0]
    start = info.header_offset + 30 + len(info.filename.encode()) + 9  # past the LZMA header
    for i in range(start, start + 40):
        data[i] ^= 0xFF
    return bytes(data)


def test_corrupt_lzma_epub_entry_is_422_not_500(client):
    lzma = pytest.importorskip("lzma")
    data = _corrupt_lzma_epub()
    with zipfile.ZipFile(io.BytesIO(data)) as z, pytest.raises(lzma.LZMAError):
        z.read("OEBPS/a.xhtml")  # the crafted entry really does raise LZMAError
    did = _drama()
    r = _up(client, _raw(did), data, name="b.epub")
    assert r.status_code == 422, r.text
    assert "not a valid EPUB" in r.json()["error"]["message"]
    r = client.post(f"/api/novel/dramas/{did}/attach-epub",
                    files={"file": ("b.epub", data, "application/epub+zip")})
    assert r.status_code == 422, r.text
    assert "not a valid EPUB" in r.json()["error"]["message"]
