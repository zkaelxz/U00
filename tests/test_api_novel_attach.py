"""Tests for /api/novel (Migration Slice 38): fully mocked, tiny in-test EPUB,
fake OCR, isolated library."""

import io
import os
import time
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import background_jobs
import ocr
from api.api_config import ApiSettings
from api.server import create_app
from services import novel_attach_service as svc


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(svc.importlib.util, "find_spec", lambda name: object())
    # X-Baihe-Local: what the React upload helper sends; local_only refuses
    # a multipart POST without it (api/auth.py _cross_site_safe)
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _drama():
    import db
    return db.create_drama(title_en="D")


def _epub(entries=None, symlink=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if entries is None:
            z.writestr("META-INF/container.xml",
                       '<container><rootfiles><rootfile full-path="OEBPS/c.opf"/></rootfiles></container>')
            z.writestr("OEBPS/c.opf",
                       '<package><manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/>'
                       '</manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
            z.writestr("OEBPS/a.xhtml", "<html><body><p>Second</p><script>x()</script></body></html>")
            z.writestr("OEBPS/b.xhtml", "<html><body><p>First &amp; more</p></body></html>")
        else:
            for name, data in entries.items():
                z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16
            z.writestr(info, "target")
    return buf.getvalue()


def _novel_file(did):
    import db
    with open(os.path.join(db.drama_dir(did), "novel_narration_source.txt"), encoding="utf-8") as f:
        return f.read()


def test_attach_text_and_status(client):
    did = _drama()
    assert client.get(f"/api/novel/dramas/{did}/status").json() == {
        "drama_id": did, "has_novel_text": False, "char_count": 0, "chapters": 0,
        "ocr_running": False}
    r = client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "one\n\ntwo"})
    assert r.status_code == 200 and r.json() == {"char_count": 8}
    assert _novel_file(did) == "one\n\ntwo"
    s = client.get(f"/api/novel/dramas/{did}/status").json()
    assert s["has_novel_text"] and s["chapters"] == 2
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "three", "mode": "append"})
    assert _novel_file(did) == "one\n\ntwo\n\nthree"


def test_attach_text_errors(client, monkeypatch):
    did = _drama()
    assert client.post("/api/novel/dramas/999/attach-text", json={"text": "x"}).status_code == 404
    assert client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "  "}).status_code == 422
    assert client.post(f"/api/novel/dramas/{did}/attach-text",
                       json={"text": "x", "mode": "zap"}).status_code == 422
    monkeypatch.setattr(svc, "MAX_TEXT_CHARS", 3)
    assert client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "abcd"}).status_code == 422
    monkeypatch.setattr(svc.drama_service, "job_running_for_drama", lambda d: True)
    assert client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "a"}).status_code == 409


def test_attach_epub_reading_order_plain_text(client):
    did = _drama()
    r = client.post(f"/api/novel/dramas/{did}/attach-epub", files={"file": ("../evil.epub", _epub())})
    assert r.status_code == 200
    assert _novel_file(did) == "First & more\n\nSecond"
    import db
    assert not [n for n in os.listdir(db.drama_dir(did)) if n.endswith(".epub")]
    assert "evil" not in r.text


@pytest.mark.parametrize("entries,symlink", [
    ({"../x.xhtml": "<p>a</p>"}, None),
    ({"/abs.xhtml": "<p>a</p>"}, None),
    ({"a/../../x.xhtml": "<p>a</p>"}, None),
    ({"a.xhtml": "<p>a</p>"}, "link.xhtml"),
    ({"readme.txt": "no html"}, None),
])
def test_attach_epub_rejects_bad(client, entries, symlink):
    did = _drama()
    r = client.post(f"/api/novel/dramas/{did}/attach-epub",
                    files={"file": ("a.epub", _epub(entries, symlink))})
    assert r.status_code == 422
    import db
    assert not os.path.exists(os.path.join(db.drama_dir(did), "novel_narration_source.txt"))


def test_attach_epub_not_zip_and_caps(client, monkeypatch):
    did = _drama()
    assert client.post(f"/api/novel/dramas/{did}/attach-epub",
                       files={"file": ("a.epub", b"notzip")}).status_code == 422
    monkeypatch.setattr(svc, "MAX_EPUB_ENTRIES", 1)
    assert client.post(f"/api/novel/dramas/{did}/attach-epub",
                       files={"file": ("a.epub", _epub())}).status_code == 422
    monkeypatch.setattr(svc, "MAX_EPUB_ENTRIES", 5000)
    monkeypatch.setattr(svc, "MAX_EPUB_UNCOMPRESSED", 10)
    assert client.post(f"/api/novel/dramas/{did}/attach-epub",
                       files={"file": ("a.epub", _epub())}).status_code == 422
    monkeypatch.setattr(svc, "MAX_EPUB_UNCOMPRESSED", 10**8)
    monkeypatch.setattr(svc, "MAX_EPUB_BYTES", 10)
    assert client.post(f"/api/novel/dramas/{did}/attach-epub",
                       files={"file": ("a.epub", _epub())}).status_code == 422
    assert client.post("/api/novel/dramas/999/attach-epub",
                       files={"file": ("a.epub", _epub())}).status_code == 404


def _wait(job_id):
    for _ in range(100):
        st = background_jobs.get_status(job_id)
        if st and st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_ocr_chapter_job(client, monkeypatch):
    did = _drama()
    seen = {}

    def fake_ocr(paths, backend, source_language, chinese_script, **kw):
        seen["names"] = [os.path.basename(p) for p in paths]
        seen["backend"] = backend
        return "OCR text"
    monkeypatch.setattr(ocr, "extract_text_from_images", fake_ocr)
    files = [("files", ("../a b.PNG", b"img1")), ("files", ("z.jpg", b"img2"))]
    r = client.post(f"/api/novel/dramas/{did}/ocr-chapter", files=files)
    assert r.status_code == 200 and r.json() == {"job_id": f"ocrchapter_{did}"}
    st = _wait(f"ocrchapter_{did}")
    assert st["status"] == "done" and st["result"] == {"char_count": 8, "image_count": 2}
    assert seen == {"names": ["0001.png", "0002.jpg"], "backend": "tesseract"}
    assert _novel_file(did) == "OCR text"
    import db
    assert [n for n in os.listdir(db.drama_dir(did)) if n.startswith(".ocr_")] == []


def test_ocr_chapter_passes_tesseract_cmd(client, monkeypatch):
    did = _drama()
    seen = {}

    def fake_ocr(paths, backend, source_language, chinese_script, tesseract_cmd=None, **kw):
        seen["cmd"] = tesseract_cmd
        return "OCR text"
    monkeypatch.setattr(ocr, "extract_text_from_images", fake_ocr)
    r = client.post(f"/api/novel/dramas/{did}/ocr-chapter",
                    files=[("files", ("a.png", b"img"))],
                    data={"tesseract_cmd": "D:/Tesseract/tesseract.exe"})
    assert r.status_code == 200
    _wait(f"ocrchapter_{did}")
    assert seen["cmd"] == "D:/Tesseract/tesseract.exe"


def test_ocr_chapter_errors(client, monkeypatch):
    did = _drama()
    url = f"/api/novel/dramas/{did}/ocr-chapter"
    png = [("files", ("a.png", b"x"))]
    assert client.post("/api/novel/dramas/999/ocr-chapter", files=png).status_code == 404
    assert client.post(url, files=[("files", ("a.gif", b"x"))]).status_code == 422
    assert client.post(url, files=png, data={"backend": "manga_ocr"}).status_code == 422
    monkeypatch.setattr(svc.importlib.util, "find_spec", lambda name: None)
    assert client.post(url, files=png).status_code == 503
    monkeypatch.setattr(svc.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(svc.drama_service, "job_running_for_drama", lambda d: True)
    assert client.post(url, files=png).status_code == 409
    import db
    assert [n for n in os.listdir(db.drama_dir(did)) if n.startswith(".ocr_")] == []


def test_ocr_duplicate_job_409(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
    r = client.post(f"/api/novel/dramas/{did}/ocr-chapter", files=[("files", ("a.png", b"x"))])
    assert r.status_code == 409
    import db
    assert [n for n in os.listdir(db.drama_dir(did)) if n.startswith(".ocr_")] == []
