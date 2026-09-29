"""Tests for the comic viewer routes (/api/scanlate/dramas/{id}/pages...,
/progress): fake PNG/JPEG bytes in an isolated library, no PIL, no network."""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(64))


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _write(drama_id, name, data):
    import db
    path = os.path.join(db.drama_dir(drama_id), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


@pytest.fixture
def comic(isolated_db):
    """A manga with two pages: page 1 png (with typeset output), page 2 jpg."""
    import db
    d = db.create_drama(title_en="Comic", media_type="manga")
    _write(d, "pages/page_0000.png", PNG)
    _write(d, "pages/page_0001.jpg", JPEG)
    _write(d, "pages/typeset_0000.png", PNG)
    p1 = db.create_page(d, 0, "pages/page_0000.png", 800, 1200)
    p2 = db.create_page(d, 1, "pages/page_0001.jpg", 800, 1100)
    db.update_page(p1, rendered_filename="pages/typeset_0000.png")
    db.save_bubbles(p1, [
        {"x": 1, "y": 2, "w": 30, "h": 40, "source_text": "你好", "translated_text": "Hello"},
        {"x": 5, "y": 5, "w": 5, "h": 5, "source_text": "x", "translated_text": "junk", "skip": True},
        {"x": 9, "y": 9, "w": 9, "h": 9, "source_text": "砰", "translated_text": "BANG", "kind": "sfx"},
        {"x": 7, "y": 7, "w": 7, "h": 7, "source_text": "轰", "translated_text": "BOOM", "kind": "sfx",
         "include_sfx": True},
    ])
    return {"drama": d, "p1": p1, "p2": p2}


def _img(d, p, variant=None):
    return f"/api/scanlate/dramas/{d}/pages/{p}/image" + (f"?variant={variant}" if variant else "")


def _no_leak(r, *extra):
    import db
    for s in (db.LIBRARY_DIR, db.DRAMAS_DIR, "page_0000", "page_0001", "typeset_", "pages/",
              *extra):
        assert s not in r.text, s


# --- C1 ----------------------------------------------------------------------

def test_list_pages_shape(client, comic):
    r = client.get(f"/api/scanlate/dramas/{comic['drama']}/pages")
    assert r.status_code == 200
    body = r.json()
    assert body["drama_id"] == comic["drama"] and body["media_type"] == "manga"
    assert body["reading_mode_default"] == "paged"
    assert body["page_count"] == 2 and body["chapters"] == []
    p1, p2 = body["pages"]
    assert set(p1) == {"id", "ordinal", "width", "height", "has_rendered", "has_regions",
                       "image_version"}
    assert (p1["id"], p1["ordinal"], p1["width"], p1["height"]) == (comic["p1"], 1, 800, 1200)
    assert p1["has_rendered"] is True and p1["has_regions"] is True and p1["image_version"] > 0
    assert (p2["ordinal"], p2["has_rendered"], p2["has_regions"]) == (2, False, False)
    _no_leak(r)


def test_list_pages_vertical_default_and_404(client, isolated_db):
    import db
    d = db.create_drama(title_en="W", media_type="manhwa")
    body = client.get(f"/api/scanlate/dramas/{d}/pages").json()
    assert body["reading_mode_default"] == "vertical" and body["pages"] == []
    r = client.get("/api/scanlate/dramas/9999/pages")
    assert r.status_code == 404
    _no_leak(r)


# --- C2 ----------------------------------------------------------------------

def test_image_png_jpeg_and_headers(client, comic):
    r = client.get(_img(comic["drama"], comic["p1"]))
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, no-cache"
    cd = r.headers["content-disposition"]
    assert cd.startswith("inline") and 'filename="page_1.png"' in cd
    assert r.headers["etag"] and r.headers["last-modified"]
    r = client.get(_img(comic["drama"], comic["p2"]))
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg"
    assert 'filename="page_2.jpg"' in r.headers["content-disposition"]


def test_image_rendered_variant(client, comic):
    r = client.get(_img(comic["drama"], comic["p1"], "rendered"))
    assert r.status_code == 200 and r.content == PNG
    r = client.get(_img(comic["drama"], comic["p2"], "rendered"))
    assert r.status_code == 404
    _no_leak(r)


def test_image_bad_variant_422(client, comic):
    r = client.get(_img(comic["drama"], comic["p1"], "thumb"))
    assert r.status_code == 422
    _no_leak(r)


def test_image_head(client, comic):
    r = client.head(_img(comic["drama"], comic["p1"]))
    assert r.status_code == 200 and r.content == b""
    assert r.headers["content-length"] == str(len(PNG))
    assert r.headers["content-type"] == "image/png"


def test_image_if_none_match_304(client, comic):
    url = _img(comic["drama"], comic["p1"])
    etag = client.get(url).headers["etag"]
    r = client.get(url, headers={"If-None-Match": etag})
    assert r.status_code == 304 and r.content == b""
    assert r.headers["etag"] == etag and r.headers["cache-control"] == "private, no-cache"
    assert client.get(url, headers={"If-None-Match": f'"other", W/{etag}'}).status_code == 304
    r = client.get(url, headers={"If-None-Match": '"stale"'})
    assert r.status_code == 200 and r.content == PNG


def test_image_range(client, comic):
    r = client.get(_img(comic["drama"], comic["p1"]), headers={"Range": "bytes=0-7"})
    assert r.status_code == 206 and r.content == PNG[:8]


def test_image_cross_drama_and_unknown(client, isolated_db, comic):
    import db
    other = db.create_drama(title_en="Other")
    responses = [client.get(_img(other, comic["p1"])),
                 client.get(_img(comic["drama"], 99999)),
                 client.get(_img(9999, comic["p1"]))]
    for r in responses:
        assert r.status_code == 404 and PNG not in r.content
        _no_leak(r)
    assert len({r.text for r in responses}) == 1   # one generic body


def test_image_traversal_absolute_and_symlink(client, isolated_db, comic, tmp_path):
    import db
    d = comic["drama"]
    outside = os.path.realpath(os.path.join(db.drama_dir(d), "..", "outside.png"))
    with open(outside, "wb") as f:
        f.write(PNG + b"OUTSIDE")
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG + b"SECRET")
    generic = client.get(_img(d, 99999)).text
    for name in ("../outside.png", "pages/../../outside.png", str(secret)):
        db.update_page(comic["p1"], filename=name)
        r = client.get(_img(d, comic["p1"]))
        assert r.status_code == 404 and b"OUTSIDE" not in r.content and b"SECRET" not in r.content
        assert r.text == generic
        _no_leak(r, str(tmp_path), "outside.png")
    link = os.path.join(db.drama_dir(d), "pages", "link.png")
    inner_link = os.path.join(db.drama_dir(d), "pages", "inner.png")
    try:
        os.symlink(secret, link)
        os.symlink(os.path.join(db.drama_dir(d), "pages", "page_0000.png"), inner_link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    for name in ("pages/link.png", "pages/inner.png"):   # escaping and in-folder symlinks
        db.update_page(comic["p1"], filename=name)
        r = client.get(_img(d, comic["p1"]))
        assert r.status_code == 404 and b"SECRET" not in r.content and r.text == generic
    # a symlinked directory below the drama folder
    os.symlink(tmp_path, os.path.join(db.drama_dir(d), "linkdir"))
    db.update_page(comic["p1"], filename="linkdir/secret.png")
    assert client.get(_img(d, comic["p1"])).status_code == 404


def test_image_wrong_magic_extension_and_directory(client, isolated_db, comic):
    import db
    d = comic["drama"]
    _write(d, "pages/fake.png", b"<svg onload=alert(1)>" + bytes(40))
    _write(d, "pages/notes.txt", PNG)
    _write(d, "pages/pic.webp", PNG)
    _write(d, "pages/empty.png", b"")
    os.makedirs(os.path.join(db.drama_dir(d), "pages", "dir.png"))
    for name in ("pages/fake.png", "pages/notes.txt", "pages/pic.webp", "pages/empty.png",
                 "pages/dir.png", "", None, "pages/missing.png"):
        db.update_page(comic["p1"], filename=name)
        r = client.get(_img(d, comic["p1"]))
        assert r.status_code == 404, name
        _no_leak(r)


def test_image_content_type_from_magic_not_extension(client, isolated_db, comic):
    import db
    _write(comic["drama"], "pages/really_jpeg.png", JPEG)
    db.update_page(comic["p1"], filename="pages/really_jpeg.png")
    r = client.get(_img(comic["drama"], comic["p1"]))
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_image_size_cap(client, isolated_db, comic, monkeypatch):
    from services import comic_view_service
    monkeypatch.setattr(comic_view_service, "MAX_IMAGE_BYTES", len(PNG) - 1)
    r = client.get(_img(comic["drama"], comic["p1"]))
    assert r.status_code == 404
    _no_leak(r)
    monkeypatch.setattr(comic_view_service, "MAX_IMAGE_BYTES", len(PNG))
    assert client.get(_img(comic["drama"], comic["p1"])).status_code == 200


def test_default_cap_is_50_mb():
    from services import comic_view_service
    assert comic_view_service.MAX_IMAGE_BYTES == 50 * 1024 * 1024


def test_no_pil_in_serve_path():
    import pathlib
    src = pathlib.Path(__file__).parent.parent / "services" / "comic_view_service.py"
    text = src.read_text(encoding="utf-8")
    assert "PIL" not in text.replace("(no PIL)", "") and "import scanlate" not in text


def test_commonpath_valueerror_is_404(client, comic, monkeypatch):
    from services import comic_view_service as cvs

    def boom(paths):
        raise ValueError("Paths don't have the same drive")
    monkeypatch.setattr(cvs.os.path, "commonpath", boom)
    assert client.get(_img(comic["drama"], comic["p1"])).status_code == 404


# --- C3 ----------------------------------------------------------------------

def test_regions_filter_skip_and_sfx(client, comic):
    r = client.get(f"/api/scanlate/dramas/{comic['drama']}/pages/{comic['p1']}/regions")
    assert r.status_code == 200
    body = r.json()
    assert (body["page_id"], body["width"], body["height"]) == (comic["p1"], 800, 1200)
    texts = [g["translated_text"] for g in body["regions"]]
    assert texts == ["Hello", "BOOM"]
    first = body["regions"][0]
    assert first == {"idx": 0, "x": 1, "y": 2, "w": 30, "h": 40, "translated_text": "Hello",
                     "source_text": "你好", "kind": "bubble"}
    assert "id" not in first
    _no_leak(r)


def test_regions_other_drama_404(client, isolated_db, comic):
    import db
    other = db.create_drama(title_en="Other")
    r = client.get(f"/api/scanlate/dramas/{other}/pages/{comic['p1']}/regions")
    assert r.status_code == 404
    assert client.get(f"/api/scanlate/dramas/9999/pages/{comic['p1']}/regions").status_code == 404
    empty = client.get(f"/api/scanlate/dramas/{comic['drama']}/pages/{comic['p2']}/regions")
    assert empty.status_code == 200 and empty.json()["regions"] == []


# --- C4 / C5 -----------------------------------------------------------------

def test_progress_default_and_write(client, isolated_db, comic):
    import db
    d = comic["drama"]
    url = f"/api/scanlate/dramas/{d}/progress"
    assert client.get(url).json() == {"last_page": 1, "percent_complete": 0.0}
    db.save_progress(d, last_line_idx=7, audio_position_seconds=12.5)
    r = client.post(url, json={"page": 1})
    assert r.status_code == 200 and r.json() == {"last_page": 1, "percent_complete": 50.0}
    assert client.get(url).json() == {"last_page": 1, "percent_complete": 50.0}
    client.post(url, json={"page": 2})
    row = db.get_progress(d)
    assert row["last_page"] == 2 and row["percent_complete"] == 100.0
    assert row["last_line_idx"] == 7 and row["audio_position_seconds"] == 12.5


def test_progress_422_and_404(client, isolated_db, comic):
    import db
    d = comic["drama"]
    url = f"/api/scanlate/dramas/{d}/progress"
    for body in ({"page": 3}, {"page": 0}, {"page": "x"}, {}, {"page": 1, "extra": 1}):
        assert client.post(url, json=body).status_code == 422, body
    assert db.get_progress(d) is None
    empty = db.create_drama(title_en="Empty")
    assert client.post(f"/api/scanlate/dramas/{empty}/progress", json={"page": 1}).status_code == 422
    assert client.get("/api/scanlate/dramas/9999/progress").status_code == 404
    assert client.post("/api/scanlate/dramas/9999/progress", json={"page": 1}).status_code == 404


# --- permissions ---------------------------------------------------------------

def test_permissions_with_auth_on(isolated_db, comic):
    from api import auth as api_auth
    from services import auth_service
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)

    def headers(*perms, email):
        """A user holding exactly `perms` (household defaults revoked first)."""
        u = auth_service.add_user(email)
        for p in auth_service.effective_permissions(u["id"]):
            auth_service.revoke_permission(u["id"], p)
        for p in perms:
            auth_service.grant_permission(u["id"], p)
        s = auth_service.create_session(u["id"])
        return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                api_auth.CSRF_HEADER: s["csrf_token"]}
    d, p1 = comic["drama"], comic["p1"]
    routes = [("get", f"/api/scanlate/dramas/{d}/pages", "library.read", None),
              ("get", _img(d, p1), "media.stream", None),
              ("head", _img(d, p1), "media.stream", None),
              ("get", f"/api/scanlate/dramas/{d}/pages/{p1}/regions", "lines.read", None),
              ("get", f"/api/scanlate/dramas/{d}/progress", "library.read", None),
              ("post", f"/api/scanlate/dramas/{d}/progress", "lines.edit", {"page": 1})]
    for i, (method, url, perm, body) in enumerate(routes):
        kw = {"json": body} if body is not None else {}
        assert getattr(c, method)(url, **kw).status_code == 401, url
        others = [p for p in ("library.read", "lines.read", "lines.edit", "media.stream")
                  if p != perm]
        no = headers(*others, email=f"no{i}@example.com")
        assert getattr(c, method)(url, headers=no, **kw).status_code == 403, url
        yes = headers(perm, email=f"yes{i}@example.com")
        assert getattr(c, method)(url, headers=yes, **kw).status_code == 200, url
