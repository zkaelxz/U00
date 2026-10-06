"""Workspace preamble/Source parity (react-misc-parity): romanize credits
(P13), cover art (P14), EPUB chapter range (S13) and using the chapters
imported in Sources as narration text (S12). Engines are faked; images and
EPUBs are built in the test; isolated library, no network."""
import io
import os
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import db
import translation_guide
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, metadata_service
from services import novel_attach_service as novel_svc

LOCAL = {"X-Baihe-Local": "1"}


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False, headers=LOCAL)


def _drama(**kw):
    return db.create_drama(title_en="D", **kw)


# --- P13 romanize credits ----------------------------------------------------

class FakeEngine:
    supports_reference = True
    model = "fake-model"


@pytest.fixture
def engine(monkeypatch):
    calls = []
    monkeypatch.setattr(metadata_service.settings_service, "resolve_key", lambda k: "key")
    monkeypatch.setattr(metadata_service.translate_engines, "get_engine",
                        lambda name, key, **kw: calls.append(name) or FakeEngine())

    def fake_romanize(meta, eng, source_language="zh", usage_cb=None):
        calls.append(("romanize", dict(meta), source_language))
        if usage_cb:
            usage_cb(100, 20)
        return {"author": "Mo Xiang Tong Xiu", "studio": "JJWXC", "director": "ignored",
                "bogus": "x"}
    monkeypatch.setattr(translation_guide, "romanize_metadata", fake_romanize)
    return calls


class TestRomanize:
    def test_writes_only_romanized_fields_for_present_credits(self, client, engine):
        did = _drama(author="墨香铜臭", studio="晋江文学城", source_language="zh")
        r = client.post(f"/api/metadata/dramas/{did}/romanize-credits", json={"engine": "claude"})
        assert r.status_code == 200, r.text
        assert r.json() == {"drama_id": did, "updated": True,
                            "romanized": {"author": "Mo Xiang Tong Xiu", "studio": "JJWXC"}}
        d = db.get_drama(did)
        assert d["author"] == "墨香铜臭" and d["author_romanized"] == "Mo Xiang Tong Xiu"
        assert d["studio_romanized"] == "JJWXC" and d["director_romanized"] is None
        assert client.get(f"/api/library/dramas/{did}").json()["author_romanized"] == "Mo Xiang Tong Xiu"
        assert ("romanize", {"author": "墨香铜臭", "studio": "晋江文学城", "director": "",
                             "voice_actors": ""}, "zh") in engine
        assert db.get_usage_summary()["call_count"] == 1

    def test_a_removed_credit_loses_its_old_romanized_form(self, client, engine):
        did = _drama(author="墨香铜臭", director_romanized="Zhang San")   # director since removed
        assert client.post(f"/api/metadata/dramas/{did}/romanize-credits", json={}).status_code == 200
        assert db.get_drama(did)["director_romanized"] is None

    def test_defaults_to_the_drama_engine(self, client, engine):
        did = _drama(author="A", translation_engine="deepseek")
        assert client.post(f"/api/metadata/dramas/{did}/romanize-credits", json={}).status_code == 200
        assert engine[0] == "deepseek"

    def test_errors(self, client, engine, monkeypatch):
        did = _drama()
        r = client.post(f"/api/metadata/dramas/{did}/romanize-credits", json={})
        assert r.status_code == 422                       # no credits
        assert client.post("/api/metadata/dramas/999/romanize-credits", json={}).status_code == 404
        did = _drama(author="A")
        assert client.post(f"/api/metadata/dramas/{did}/romanize-credits",
                           json={"engine": "nllb"}).status_code == 422   # not an LLM
        assert client.post(f"/api/metadata/dramas/{did}/romanize-credits",
                           json={"key": "x"}).status_code == 422           # extra field
        monkeypatch.setattr(metadata_service.settings_service, "resolve_key", lambda k: None)
        assert client.post(f"/api/metadata/dramas/{did}/romanize-credits",
                           json={"engine": "claude"}).status_code == 503

    def test_nothing_usable_writes_nothing(self, client, engine, monkeypatch):
        monkeypatch.setattr(translation_guide, "romanize_metadata", lambda *a, **k: {})
        did = _drama(author="A")
        r = client.post(f"/api/metadata/dramas/{did}/romanize-credits", json={"engine": "claude"})
        assert r.status_code == 200 and r.json()["updated"] is False
        assert db.get_drama(did)["author_romanized"] is None

    def test_engine_check_runs_for_admin_library_without_engines_paid(self, isolated_db, engine,
                                                                       monkeypatch):
        # Admins hold every permission today; this pins the route's own
        # require_engines_allowed check in case admin.library is ever split out.
        did = _drama(author="A")
        app = create_app(ApiSettings(auth_mode="on"))
        remote = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
        u = auth_service.add_user("lib@example.com")
        monkeypatch.setattr(auth_service, "effective_permissions",
                            lambda uid: ["admin.library", "library.read"])
        s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
        from api import auth as api_auth
        h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}
        url = f"/api/metadata/dramas/{did}/romanize-credits"
        assert remote.post(url, json={"engine": "claude"}, headers=h).status_code == 403
        assert remote.post(url, json={}, headers=h).status_code == 403          # drama default: claude
        assert db.get_drama(did)["author_romanized"] is None
        assert remote.post(url, json={"engine": "ollama"}, headers=h).status_code == 200

    def test_paid_engine_needs_engines_paid(self, isolated_db, engine):
        did = _drama(author="A")
        app = create_app(ApiSettings(auth_mode="on"))
        remote = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
        u = auth_service.add_user("kid@example.com")
        auth_service.grant_permission(u["id"], "library.read")
        s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
        from api import auth as api_auth
        h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}
        assert remote.post(f"/api/metadata/dramas/{did}/romanize-credits", json={"engine": "claude"},
                           headers=h).status_code == 403
        assert db.get_drama(did)["author_romanized"] is None


# --- P14 cover art -----------------------------------------------------------

def _image(fmt="PNG", size=(8, 12), exif=False):
    from PIL import Image
    img = Image.new("RGB", size, (200, 10, 10))
    buf = io.BytesIO()
    kw = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretCamera"          # Make
        ex[0x9003] = "2020:01:01 00:00:00"   # DateTimeOriginal
        kw["exif"] = ex.tobytes()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


class TestCover:
    def test_upload_strips_metadata_and_serves(self, client):
        pytest.importorskip("PIL")
        did = _drama()
        data = _image("JPEG", exif=True)
        assert b"SecretCamera" in data
        r = client.post(f"/api/dramas/{did}/cover", files={"file": ("x.png", data, "image/png")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"drama_id": did, "has_cover_art": True, "format": "jpg", "width": 8,
                        "height": 12, "size_bytes": body["size_bytes"]}
        assert db.get_drama(did)["cover_art_filename"] == "cover.jpg"
        assert client.get(f"/api/library/dramas/{did}").json()["has_cover_art"] is True
        g = client.get(f"/api/dramas/{did}/cover")
        assert g.status_code == 200 and g.headers["content-type"] == "image/jpeg"
        assert g.headers["x-content-type-options"] == "nosniff"
        assert b"SecretCamera" not in g.content and b"Exif" not in g.content

    def test_exif_rotation_is_applied_before_the_tag_is_dropped(self, client):
        pytest.importorskip("PIL")
        from PIL import Image
        img = Image.new("RGB", (8, 12), (0, 0, 255))
        ex = Image.Exif()
        ex[0x0112] = 6                      # Orientation: rotate 90 degrees
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=ex.tobytes())
        did = _drama()
        r = client.post(f"/api/dramas/{did}/cover", files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")})
        assert r.status_code == 200 and (r.json()["width"], r.json()["height"]) == (12, 8)
        stored = Image.open(io.BytesIO(client.get(f"/api/dramas/{did}/cover").content))
        assert stored.size == (12, 8) and not stored.getexif()

    def test_replacing_with_another_format_removes_the_old_file(self, client):
        pytest.importorskip("PIL")
        did = _drama()
        client.post(f"/api/dramas/{did}/cover", files={"file": ("a.jpg", _image("JPEG"), "image/jpeg")})
        r = client.post(f"/api/dramas/{did}/cover", files={"file": ("a.webp", _image("WEBP"), "image/webp")})
        assert r.status_code == 200 and r.json()["format"] == "webp"
        folder = db.drama_dir(did)
        assert os.path.exists(os.path.join(folder, "cover.webp"))
        assert not os.path.exists(os.path.join(folder, "cover.jpg"))

    def test_rejects_non_images_and_oversize(self, client, monkeypatch):
        pytest.importorskip("PIL")
        from services import cover_art_service
        did = _drama()
        for payload in (b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"GIF89a....", b"", b"not an image"):
            r = client.post(f"/api/dramas/{did}/cover", files={"file": ("c.png", payload, "image/png")})
            assert r.status_code == 422, payload
        gif = io.BytesIO()
        from PIL import Image
        Image.new("RGB", (2, 2)).save(gif, "GIF")
        assert client.post(f"/api/dramas/{did}/cover",
                           files={"file": ("c.gif", gif.getvalue(), "image/gif")}).status_code == 422
        monkeypatch.setattr(cover_art_service, "MAX_COVER_PIXELS", 50)
        assert client.post(f"/api/dramas/{did}/cover",
                           files={"file": ("c.png", _image(size=(10, 10)), "image/png")}).status_code == 422
        monkeypatch.setattr(cover_art_service, "MAX_COVER_BYTES", 10)
        assert client.post(f"/api/dramas/{did}/cover",
                           files={"file": ("c.png", _image(), "image/png")}).status_code == 422
        assert db.get_drama(did)["cover_art_filename"] is None
        assert client.get(f"/api/dramas/{did}/cover").status_code == 404

    def test_only_png_jpeg_webp_parsers_see_an_upload(self, client, monkeypatch):
        pytest.importorskip("PIL")
        from PIL import Image
        seen = []
        real_open = Image.open

        def spy(fp, mode="r", formats=None):
            seen.append(formats)
            return real_open(fp, mode, formats)
        monkeypatch.setattr(Image, "open", spy)
        did = _drama()
        tiff = io.BytesIO()
        Image.new("RGB", (2, 2)).save(tiff, "TIFF")
        eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 2 2\n"
        for name, payload in (("c.tif", tiff.getvalue()), ("c.eps", eps)):
            r = client.post(f"/api/dramas/{did}/cover", files={"file": (name, payload, "image/png")})
            assert r.status_code == 422, name
        assert seen and all(f is not None and set(f) == {"PNG", "JPEG", "WEBP"} for f in seen)
        assert db.get_drama(did)["cover_art_filename"] is None

    def test_content_length_over_the_cap_is_refused_before_the_body_is_read(self, client, monkeypatch):
        pytest.importorskip("PIL")
        from api.routers import drama_routes
        from services import cover_art_service
        did = _drama()
        monkeypatch.setattr(cover_art_service, "MAX_COVER_BYTES", 10)
        monkeypatch.setattr(drama_routes, "_COVER_MULTIPART_OVERHEAD", 0)
        monkeypatch.setattr(drama_routes, "capped", lambda *a: (_ for _ in ()).throw(
            AssertionError("body read")))
        r = client.post(f"/api/dramas/{did}/cover", files={"file": ("c.png", _image(), "image/png")})
        assert r.status_code == 413
        assert db.get_drama(did)["cover_art_filename"] is None

    def test_chunked_or_no_file_field_is_refused(self, client):
        did = _drama()

        def chunks():
            yield b"--x\r\n"
        r = client.post(f"/api/dramas/{did}/cover", content=chunks(),
                        headers={"content-type": "multipart/form-data; boundary=x"})
        assert r.status_code == 422
        r = client.post(f"/api/dramas/{did}/cover", files={"other": ("c.png", b"x", "image/png")})
        assert r.status_code == 422
        assert db.get_drama(did)["cover_art_filename"] is None

    def test_replacing_a_legacy_upper_case_cover_keeps_the_new_file(self, client):
        # Streamlit kept the client's extension ("cover.JPG"); on Windows/macOS
        # that is the same file as the new "cover.jpg", so it must not be removed.
        pytest.importorskip("PIL")
        did = _drama()
        folder = db.drama_dir(did)
        with open(os.path.join(folder, "cover.JPG"), "wb") as f:
            f.write(_image("JPEG"))
        db.update_drama(did, cover_art_filename="cover.JPG")
        r = client.post(f"/api/dramas/{did}/cover", files={"file": ("a.jpg", _image("JPEG"), "image/jpeg")})
        assert r.status_code == 200
        assert os.path.exists(os.path.join(folder, "cover.JPG"))   # not removed
        assert client.get(f"/api/dramas/{did}/cover").status_code == 200

    def test_legacy_or_odd_stored_names_are_not_served(self, client):
        did = _drama()
        folder = db.drama_dir(did)
        with open(os.path.join(folder, "cover.html"), "w") as f:
            f.write("<script>alert(1)</script>")
        db.update_drama(did, cover_art_filename="cover.html")
        assert client.get(f"/api/dramas/{did}/cover").status_code == 404
        db.update_drama(did, cover_art_filename="../../x/cover.png")
        assert client.get(f"/api/dramas/{did}/cover").status_code == 404
        with open(os.path.join(folder, "cover.png"), "wb") as f:
            f.write(b"\x89PNG legacy")
        db.update_drama(did, cover_art_filename="cover.png")
        r = client.get(f"/api/dramas/{did}/cover")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"

    def test_upload_is_pc_only(self, isolated_db):
        did = _drama()
        remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                            raise_server_exceptions=False)
        r = remote.post(f"/api/dramas/{did}/cover", files={"file": ("c.png", b"x", "image/png")})
        assert r.status_code in (401, 403)


# --- S13 EPUB chapter range, S12 chapters from Sources -------------------------

def _epub(n=4):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container><rootfiles><rootfile full-path="OEBPS/c.opf"/></rootfiles></container>')
        items = "".join(f'<item id="c{i}" href="c{i}.xhtml"/>' for i in range(1, n + 1))
        spine = "".join(f'<itemref idref="c{i}"/>' for i in range(1, n + 1))
        z.writestr("OEBPS/c.opf", f"<package><manifest>{items}</manifest><spine>{spine}</spine></package>")
        for i in range(1, n + 1):
            z.writestr(f"OEBPS/c{i}.xhtml", f"<html><body><p>Chapter {i}</p></body></html>")
    return buf.getvalue()


def _narration(did):
    with open(os.path.join(db.drama_dir(did), "novel_narration_source.txt"), encoding="utf-8") as f:
        return f.read()


class TestEpubRange:
    def _post(self, client, did, **form):
        return client.post(f"/api/novel/dramas/{did}/attach-epub",
                           files={"file": ("b.epub", _epub(), "application/epub+zip")},
                           data={k: str(v) for k, v in form.items()})

    def test_range_picks_chapters(self, client):
        did = _drama()
        r = self._post(client, did, chapter_from=2, chapter_to=3)
        assert r.status_code == 200, r.text
        assert r.json() == {"char_count": len("Chapter 2\n\nChapter 3"), "epub_chapters": 4,
                            "chapter_from": 2, "chapter_to": 3}
        assert _narration(did) == "Chapter 2\n\nChapter 3"

    def test_open_ended_and_whole_book(self, client):
        did = _drama()
        assert self._post(client, did, chapter_from=4).json()["chapter_to"] == 4
        assert _narration(did) == "Chapter 4"
        body = self._post(client, did).json()
        assert (body["chapter_from"], body["chapter_to"], body["epub_chapters"]) == (1, 4, 4)

    def test_out_of_range_says_how_many(self, client):
        did = _drama()
        r = self._post(client, did, chapter_from=3, chapter_to=9)
        assert r.status_code == 422 and "4 chapters" in r.text
        assert self._post(client, did, chapter_from=3, chapter_to=2).status_code == 422
        assert self._post(client, did, chapter_from=0).status_code == 422
        assert not os.path.exists(os.path.join(db.drama_dir(did), "novel_narration_source.txt"))

    def test_extract_epub_text_unchanged(self):
        assert novel_svc.extract_epub_text(io.BytesIO(_epub(2))) == "Chapter 1\n\nChapter 2"


class TestFromSources:
    def test_uses_raw_novel_context(self, client):
        did = _drama()
        url = f"/api/novel/dramas/{did}/attach-from-sources"
        assert client.post(url, json={}).status_code == 404
        with open(os.path.join(db.drama_dir(did), "raw_novel_context.txt"), "w", encoding="utf-8") as f:
            f.write("第一章\n\n第二章\n")
        r = client.post(url, json={})
        assert r.status_code == 200 and r.json() == {"char_count": len("第一章\n\n第二章")}
        assert _narration(did) == "第一章\n\n第二章"
        client.post(url, json={"mode": "append"})
        assert _narration(did) == "第一章\n\n第二章\n\n第一章\n\n第二章"
        assert client.post(url, json={"mode": "bogus"}).status_code == 422

    def test_too_large_is_refused_not_truncated(self, client, monkeypatch):
        monkeypatch.setattr(novel_svc, "MAX_TEXT_CHARS", 10)
        did = _drama()
        with open(os.path.join(db.drama_dir(did), "raw_novel_context.txt"), "w", encoding="utf-8") as f:
            f.write("0123456789\n")      # 11 chars; cleaning would drop the newline
        r = client.post(f"/api/novel/dramas/{did}/attach-from-sources", json={})
        assert r.status_code == 422
        assert not os.path.exists(os.path.join(db.drama_dir(did), "novel_narration_source.txt"))

    def test_pc_only(self, isolated_db):
        did = _drama()
        remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                            raise_server_exceptions=False)
        assert remote.post(f"/api/novel/dramas/{did}/attach-from-sources", json={}).status_code in (401, 403)
