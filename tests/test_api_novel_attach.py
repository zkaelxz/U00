"""Tests for /api/novel (Migration Slice 38): fully mocked, tiny in-test EPUB,
fake OCR, isolated library."""

import io
import os
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

from jobs import job_launch
import ocr
from api.api_config import ApiSettings
from api.server import create_app
from services import novel_attach_service as svc
from tests.job_helpers import wait_for_job


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: object())
    # The Tesseract program, not only pytesseract, must be there to start.
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr.proc, "run_captured",
                        lambda cmd, timeout, **kw: ocr.proc.CapturedRun(0, "tesseract 5", "", False, False))
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
    assert s["has_novel_text"] and s["chapters"] == 1
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "three", "mode": "append"})
    assert _novel_file(did) == "one\n\ntwo\n\nthree"


PASTED = "第一章 开始\n\n甲说了话。\n\n乙回答了。\n\n第二章 继续\n\n丙走了。\n\n丁来了。\n\n戊笑了。"


def test_pasted_chapters_are_counted_by_heading_not_paragraph(client):
    did = _drama()
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": PASTED})
    assert client.get(f"/api/novel/dramas/{did}/status").json()["chapters"] == 2


def test_pasted_text_shows_as_saved_chapters_on_the_title(client):
    did = _drama()
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": PASTED})
    listed = client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()
    assert listed["present"] and listed["total"] == 2


def test_pasting_keeps_chapters_already_saved(client):
    import db
    did = _drama()
    raw = os.path.join(db.drama_dir(did), "raw_novel_context.txt")
    with open(raw, "w", encoding="utf-8") as f:
        f.write("scraped")
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": PASTED})
    with open(raw, encoding="utf-8") as f:
        assert f.read() == "scraped"


def _raw_path(did):
    import db
    return os.path.join(db.drama_dir(did), "raw_novel_context.txt")


def test_status_of_a_title_with_no_text_counts_zero_chapters(client):
    did = _drama()
    s = client.get(f"/api/novel/dramas/{did}/status").json()
    assert s["chapters"] == 0 and not s["has_novel_text"]


def test_status_of_text_without_headings_is_one_chapter(client):
    did = _drama()
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "a\n\nb\n\nc"})
    assert client.get(f"/api/novel/dramas/{did}/status").json()["chapters"] == 1


def test_crlf_pasted_text_counts_and_lists_chapters(client):
    did = _drama()
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": PASTED.replace("\n", "\r\n")})
    assert client.get(f"/api/novel/dramas/{did}/status").json()["chapters"] == 2
    assert client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()["total"] == 2


def test_status_and_title_page_agree_on_front_matter_before_the_first_heading(client):
    did = _drama()
    text = "我的小说\n作者某某\n\n第一章 开始\n\n甲说了话。\n\n第二章 继续\n\n乙走了。"
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": text})
    status = client.get(f"/api/novel/dramas/{did}/status").json()["chapters"]
    # The saved list gives the title-page preamble its own entry, so the count does too.
    listed = client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()
    assert listed["present"] and listed["total"] == 3 == status


def test_append_to_existing_translation_text_keeps_both_parts(client):
    did = _drama()
    import db
    os.makedirs(db.drama_dir(did), exist_ok=True)
    with open(os.path.join(db.drama_dir(did), "novel_narration_source.txt"), "w", encoding="utf-8") as f:
        f.write("old text\n")
    r = client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "new text", "mode": "append"})
    assert r.status_code == 200
    assert _novel_file(did) == "old text\n\nnew text"
    assert r.json()["char_count"] == len("old text\n\nnew text")


def test_append_does_not_replace_saved_raw_chapters(client):
    did = _drama()
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": PASTED})
    with open(_raw_path(did), encoding="utf-8") as f:
        first = f.read()
    client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "extra", "mode": "append"})
    with open(_raw_path(did), encoding="utf-8") as f:
        assert f.read() == first


def test_attach_refused_while_a_job_runs_writes_no_raw_file(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(svc.drama_service, "job_running_for_drama", lambda d: True)
    assert client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": PASTED}).status_code == 409
    assert not os.path.exists(_raw_path(did))
    assert client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()["present"] is False


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
    st = wait_for_job(f"ocrchapter_{did}", timeout=5.0)
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
    wait_for_job(f"ocrchapter_{did}", timeout=5.0)
    assert seen["cmd"] == "D:/Tesseract/tesseract.exe"


def test_ocr_chapter_falls_back_to_settings_tesseract_path(client, monkeypatch):
    """B-03 leftover: with no tesseract_cmd in the request, the saved
    Settings path is used (a Windows install off PATH)."""
    from services import settings_service
    settings_service.set_settings({"tesseract_cmd": "C:/Program Files/Tesseract/tesseract.exe"})
    did = _drama()
    seen = {}

    def fake_ocr(paths, backend, source_language, chinese_script, tesseract_cmd=None, **kw):
        seen["cmd"] = tesseract_cmd
        seen["backend"] = backend
        return "OCR text"
    monkeypatch.setattr(ocr, "extract_text_from_images", fake_ocr)
    r = client.post(f"/api/novel/dramas/{did}/ocr-chapter", files=[("files", ("a.png", b"img"))])
    assert r.status_code == 200
    assert wait_for_job(f"ocrchapter_{did}", timeout=5.0)["status"] == "done"
    assert seen["cmd"] == "C:/Program Files/Tesseract/tesseract.exe"
    assert seen["backend"] == "tesseract"  # default backend unchanged (Streamlit parity)


def test_ocr_chapter_errors(client, monkeypatch):
    did = _drama()
    url = f"/api/novel/dramas/{did}/ocr-chapter"
    png = [("files", ("a.png", b"x"))]
    assert client.post("/api/novel/dramas/999/ocr-chapter", files=png).status_code == 404
    assert client.post(url, files=[("files", ("a.gif", b"x"))]).status_code == 422
    assert client.post(url, files=png, data={"backend": "manga_ocr"}).status_code == 422
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: None)
    assert client.post(url, files=png).status_code == 503
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(svc.drama_service, "job_running_for_drama", lambda d: True)
    assert client.post(url, files=png).status_code == 409
    import db
    assert [n for n in os.listdir(db.drama_dir(did)) if n.startswith(".ocr_")] == []


def test_ocr_duplicate_job_409(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: False)
    r = client.post(f"/api/novel/dramas/{did}/ocr-chapter", files=[("files", ("a.png", b"x"))])
    assert r.status_code == 409
    import db
    assert [n for n in os.listdir(db.drama_dir(did)) if n.startswith(".ocr_")] == []


def _epub_with_dangling_spine():
    return _epub({
        "META-INF/container.xml":
            '<container><rootfiles><rootfile full-path="c.opf"/></rootfiles></container>',
        "c.opf": '<package><manifest><item id="a" href="a.xhtml"/><item id="m" href="gone.xhtml"/>'
                 '<item id="u" href="../up.xhtml"/></manifest>'
                 '<spine><itemref idref="a"/><itemref idref="m"/><itemref idref="u"/>'
                 '<itemref idref="nope"/></spine></package>',
        "a.xhtml": "<html><body><p>Only</p></body></html>"})


def test_attach_epub_warns_about_chapters_it_could_not_find(client):
    did = _drama()
    r = client.post(f"/api/novel/dramas/{did}/attach-epub",
                    files={"file": ("b.epub", _epub_with_dangling_spine())})
    assert r.status_code == 200, r.text
    assert "3 chapters" in r.json()["warning"]


def test_attach_epub_without_dropped_chapters_has_no_warning(client):
    did = _drama()
    r = client.post(f"/api/novel/dramas/{did}/attach-epub", files={"file": ("b.epub", _epub())})
    assert "warning" not in r.json()


def test_attach_text_over_the_limit_names_the_limit(client):
    did = _drama()
    r = client.post(f"/api/novel/dramas/{did}/attach-text", json={"text": "a" * 2_000_001})
    assert r.status_code == 422
    assert "2,000,000" in r.json()["error"]["message"]


def test_ocr_chapter_refuses_paddle_without_paddlepaddle(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(ocr.importlib.util, "find_spec",
                        lambda name: object() if name == "paddleocr" else None)
    r = client.post(f"/api/novel/dramas/{did}/ocr-chapter", files=[("files", ("a.png", b"x"))],
                    data={"backend": "paddle"})
    assert r.status_code == 503, r.text
    assert "paddlepaddle" in r.text


def test_ocr_chapter_refuses_tesseract_without_its_program(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: None)
    r = client.post(f"/api/novel/dramas/{did}/ocr-chapter", files=[("files", ("a.png", b"x"))])
    assert r.status_code == 503, r.text
    assert "tesseract program" in r.text


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_ocr_chapter_skips_an_unreadable_image_instead_of_failing(client, monkeypatch):
    did = _drama()
    read = []

    def fake_tesseract(path, **kw):
        read.append(os.path.basename(path))
        return f"page {len(read)}"
    monkeypatch.setattr(ocr, "extract_text_tesseract", fake_tesseract)
    files = [("files", ("1.png", _png())), ("files", ("2.png", b"not an image")),
             ("files", ("3.png", _png()[:40]))]   # renamed junk, then a truncated file
    r = client.post(f"/api/novel/dramas/{did}/ocr-chapter", files=files)
    assert r.status_code == 200
    st = wait_for_job(f"ocrchapter_{did}", timeout=5.0)
    assert st["status"] == "done", st.get("error")
    assert st["result"] == {"char_count": 6, "image_count": 3, "skipped_pages": 2}
    assert read == ["0001.png"]
    assert _novel_file(did) == "page 1"


def test_ocr_chapter_with_no_readable_image_fails_without_a_path(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(ocr, "extract_text_tesseract", lambda path, **kw: "never")
    r = client.post(f"/api/novel/dramas/{did}/ocr-chapter", files=[("files", ("a.png", b"junk"))])
    assert r.status_code == 200
    st = wait_for_job(f"ocrchapter_{did}", timeout=5.0)
    assert st["status"] == "error"
    assert ".ocr_" not in st["error"] and "0001" not in st["error"]
    assert st["result"]["skipped_pages"] == 1


def _opf_epub(files, items, spine, container_path="OEBPS/c.opf"):
    entries = {"META-INF/container.xml":
               f'<container><rootfiles><rootfile full-path="{container_path}"/></rootfiles></container>',
               container_path: "<package><manifest>"
               + "".join(f'<item id="{i}" href="{h}"/>' for i, h in items)
               + "</manifest><spine>" + "".join(f'<itemref idref="{i}"/>' for i in spine)
               + "</spine></package>"}
    entries.update({f"OEBPS/{name}": data for name, data in files.items()})
    return _epub(entries)


def _attach(client, did, data):
    return client.post(f"/api/novel/dramas/{did}/attach-epub", files={"file": ("b.epub", data)})


def test_attach_epub_follows_percent_encoded_chapter_links(client):
    # Sigil and most editors write a space or CJK name in an href as %XX; the zip holds it raw.
    did = _drama()
    data = _opf_epub({"cover.xhtml": "<p>Cover</p>", "第1章.xhtml": "<p>One</p>",
                      "chapter 2.xhtml": "<p>Two</p>"},
                     [("c", "cover.xhtml"), ("a", "%E7%AC%AC1%E7%AB%A0.xhtml"),
                      ("b", "chapter%202.xhtml")], ["c", "a", "b"])
    r = _attach(client, did, data)
    assert r.status_code == 200 and "warning" not in r.json()
    assert _novel_file(did) == "Cover\n\nOne\n\nTwo"


@pytest.mark.parametrize("raw", [
    '<?xml version="1.0" encoding="gbk"?><html><body><p>第一章 天下</p></body></html>'.encode("gbk"),
    '<html><head><meta charset="big5"/></head><body><p>第一章 天下</p></body></html>'.encode("big5"),
    '<html><head><meta http-equiv="Content-Type" content="text/html; charset=GB2312"/></head>'
    '<body><p>第一章 天下</p></body></html>'.encode("gb2312"),
    '<html><body><p>第一章 天下</p></body></html>'.encode("utf-16"),
], ids=["gbk-prolog", "big5-meta", "gb2312-http-equiv", "utf16-bom"])
def test_attach_epub_reads_chapters_saved_in_legacy_encodings(client, raw):
    did = _drama()
    r = _attach(client, did, _opf_epub({"c1.xhtml": raw}, [("a", "c1.xhtml")], ["a"]))
    assert r.status_code == 200
    assert _novel_file(did) == "第一章 天下"


def test_attach_epub_prefers_valid_utf8_over_a_stale_declaration(client):
    did = _drama()
    raw = '<?xml version="1.0" encoding="gbk"?><p>第一章 天下</p>'.encode("utf-8")
    assert _attach(client, did, _opf_epub({"c1.xhtml": raw}, [("a", "c1.xhtml")], ["a"])).status_code == 200
    assert _novel_file(did) == "第一章 天下"


def test_attach_epub_without_a_spine_reads_chapters_in_number_order(client):
    did = _drama()
    entries = {f"text/ch{n}.xhtml": f"<p>{n}</p>" for n in (1, 2, 10)}
    assert _attach(client, did, _epub(entries)).status_code == 200
    assert _novel_file(did) == "1\n\n2\n\n10"


def test_attached_epub_is_saved_as_the_titles_chapters_too(client):
    did = _drama()
    client.post(f"/api/novel/dramas/{did}/attach-epub", files={"file": ("a.epub", _epub())})
    listed = client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()
    assert listed["present"] and listed["char_count"] > 0


def test_epub_attach_from_a_job_is_saved_as_chapters_despite_its_own_running_job(client, monkeypatch):
    did = _drama()
    # The lightnovel-crawler job holds the title while it attaches its EPUB.
    monkeypatch.setattr(svc.drama_service, "job_running_for_drama",
                        lambda _id, exclude_job_id=None: exclude_job_id != "lncrawl_job")
    svc.attach_epub_from_job(did, io.BytesIO(_epub()), job_id="lncrawl_job")
    listed = client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()
    assert listed["present"]


def test_ocr_text_is_saved_as_the_titles_chapters_too(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(ocr, "extract_text_from_images", lambda paths, backend, source_language, chinese_script, **kw: "OCR text")
    client.post(f"/api/novel/dramas/{did}/ocr-chapter", files=[("files", ("a.png", b"img"))])
    assert wait_for_job(f"ocrchapter_{did}", timeout=5.0)["status"] == "done"
    assert client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()["present"]


def test_chapter_count_includes_front_matter_and_handles_no_headings():
    import novel_text
    assert novel_text.chapter_count("") == 0
    assert novel_text.chapter_count("a\n\nb") == 1
    assert novel_text.chapter_count("第一章\n\n甲。\n\n第二章\n\n乙。") == 2
    assert novel_text.chapter_count("书名\n\n第一章\n\n甲。\n\n第二章\n\n乙。") == 3


def _toc_epub(nav=True, docs=6, with_cover=True):
    """A book whose chapters have no headings in their text; only the table of contents names them."""
    items = ['<item id="n" href="nav.xhtml" properties="nav"/>'] if nav else ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
    items += [f'<item id="d{i}" href="d{i}.xhtml"/>' for i in range(docs)]
    if with_cover:
        items.append('<item id="cv" href="cover.png" properties="cover-image" media-type="image/png"/>')
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(docs))
    opf = ('<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<metadata><dc:title>Gentle Book</dc:title><dc:creator>An Author</dc:creator></metadata>'
           f'<manifest>{"".join(items)}</manifest><spine{"" if nav else " toc=\"ncx\""}>{spine}</spine></package>')
    entries = {
        "META-INF/container.xml": '<container><rootfiles><rootfile full-path="OEBPS/c.opf"/></rootfiles></container>',
        "OEBPS/c.opf": opf,
    }
    links = "".join(f'<li><a href="d{i}.xhtml">{i + 1} - Title {i + 1}</a></li>' for i in range(docs))
    if nav:
        entries["OEBPS/nav.xhtml"] = f'<html><body><nav epub:type="toc"><ol>{links}</ol></nav></body></html>'
    else:
        points = "".join(f'<navPoint id="p{i}"><navLabel><text>{i + 1} - Title {i + 1}</text></navLabel>'
                         f'<content src="d{i}.xhtml"/></navPoint>' for i in range(docs))
        entries["OEBPS/toc.ncx"] = f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>{points}</navMap></ncx>'
    for i in range(docs):
        entries[f"OEBPS/d{i}.xhtml"] = f"<html><body><p>Words of chapter {i + 1}.</p><p>More words.</p></body></html>"
    if with_cover:
        entries["OEBPS/cover.png"] = _png()
    return _epub(entries)


@pytest.mark.parametrize("nav", [True, False])
def test_epub_chapters_follow_its_table_of_contents(client, nav):
    did = _drama()
    assert _attach(client, did, _toc_epub(nav=nav)).status_code == 200
    listed = client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()
    assert listed["total"] == 6 and listed["split"]
    assert [c["title"] for c in listed["chapters"]][:2] == ["1 - Title 1", "2 - Title 2"]


def test_epub_without_a_table_of_contents_still_splits_by_document(client):
    did = _drama()
    assert _attach(client, did, _opf_epub({f"c{i}.xhtml": f"<p>Chapter text {i}.</p>" for i in range(3)},
                                          [(f"i{i}", f"c{i}.xhtml") for i in range(3)],
                                          [f"i{i}" for i in range(3)])).status_code == 200
    assert client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()["total"] == 3


def test_raw_novel_epub_upload_splits_by_table_of_contents(client):
    did = _drama()
    r = client.post(f"/api/novel/dramas/{did}/raw-novel", files={"file": ("b.epub", _toc_epub())})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()["total"] == 6


def test_epub_title_author_and_cover_fill_a_blank_title(client):
    import db
    did = db.create_drama(title_en="")
    assert _attach(client, did, _toc_epub()).status_code == 200
    drama = db.get_drama(did)
    assert drama["title_en"] == "Gentle Book" and drama["author"] == "An Author"
    assert drama["cover_art_filename"]


def test_epub_metadata_never_replaces_a_typed_title(client):
    import db
    did = db.create_drama(title_en="Mine", author="Me")
    _attach(client, did, _toc_epub())
    drama = db.get_drama(did)
    assert drama["title_en"] == "Mine" and drama["author"] == "Me"


def test_non_breaking_spaces_become_plain_spaces(client):
    did = _drama()
    _attach(client, did, _toc_epub(with_cover=False))
    assert " " not in _novel_file(did) and "Words of chapter 1." in _novel_file(did)
    client.post(f"/api/novel/dramas/{_drama()}/attach-text", json={"text": "a b"})


def test_one_document_epub_with_several_headings_still_splits_by_heading(client):
    did = _drama()
    body = "<p>第一章 开始</p><p>甲说了话。</p><p>第二章 继续</p><p>乙走了。</p>"
    r = client.post(f"/api/novel/dramas/{did}/raw-novel",
                    files={"file": ("b.epub", _opf_epub({"a.xhtml": body}, [("a", "a.xhtml")], ["a"]))})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()["total"] == 2
