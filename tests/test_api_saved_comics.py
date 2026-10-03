"""Saved manga: the save folder (PC only), Open folder, reading saved CBZ
chapters in the app (/api/saved-comics), the tracked series "save new
chapters as CBZ" switch, and the chapter check saving them.
Fake adapters, no network; files go to temp folders."""
import io
import os
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from services import saved_comics_service as library
from services import sources_save_service as saves
from sources import chapter_check, registry, store
from sources.models import FailureReason, SourceError
from tests.sources_helpers import png
from tests.test_api_sources_import import _chapters, _make, client, fakes  # noqa: F401

LOCAL = {"X-Baihe-Local": "1"}


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = str(tmp_path / "default")
    monkeypatch.setattr(saves, "default_root", lambda: path)
    return path


def _cbz(path, names):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for i, name in enumerate(names):
            zf.writestr(name, png(20 + i, 30, seed=i) if name.endswith(".png") else b"x")


def _library(root):
    series = os.path.join(root, "MangaK", "Test Camp")
    _cbz(os.path.join(series, "0002 Chapter 2.cbz"), ["001.png", "002.png"])
    _cbz(os.path.join(series, "0010 Chapter 10.cbz"), ["010.png", "2.png", "1.png",
                                                       "ComicInfo.xml", "__MACOSX/1.png",
                                                       ".hidden.png"])
    _cbz(os.path.join(series, "0001 Chapter 1.cbz"), ["001.png"])
    return series


def _q(**kw):
    return {"source": "MangaK", "series": "Test Camp", **kw}


# ---------------------------------------------------------------------------
# The save folder
# ---------------------------------------------------------------------------

def test_folder_defaults_then_picked_then_reset(client, root, tmp_path):
    r = client.get("/api/saved-comics/folder")
    assert r.json() == {"folder": root, "custom": False, "picked_missing": False}
    picked = tmp_path / "Manga"
    picked.mkdir()
    r = client.post("/api/saved-comics/folder", json={"folder": f"  {picked}  "})
    assert r.status_code == 200
    assert r.json() == {"folder": os.path.realpath(picked), "custom": True,
                        "picked_missing": False}
    assert saves.save_root() == os.path.realpath(picked)
    r = client.post("/api/saved-comics/folder", json={"folder": ""})
    assert r.json()["custom"] is False and saves.save_root() == root


def test_folder_must_be_an_existing_full_path(client, root, tmp_path):
    for bad in ("relative/path", str(tmp_path / "missing"), "C:\x01x"):
        r = client.post("/api/saved-comics/folder", json={"folder": bad})
        assert r.status_code == 422, bad
    assert client.post("/api/saved-comics/folder",
                       json={"folder": "/" + "x" * 1001}).status_code == 422
    assert client.get("/api/saved-comics/folder").json()["custom"] is False


def test_a_picked_folder_that_vanished_falls_back_to_the_default(client, root, tmp_path):
    picked = tmp_path / "USB"
    picked.mkdir()
    client.post("/api/saved-comics/folder", json={"folder": str(picked)})
    picked.rmdir()
    assert saves.save_root() == root
    assert client.get("/api/saved-comics/folder").json() == {
        "folder": root, "custom": False, "picked_missing": True}


def test_open_folder_creates_and_opens_it(client, root, monkeypatch):
    opened = []
    monkeypatch.setattr(saves, "_launch", opened.append)
    r = client.post("/api/saved-comics/folder/open", headers=LOCAL)
    assert r.status_code == 200 and r.json() == {"opened": True}
    assert opened == [root] and os.path.isdir(root)

    def broken(folder):
        raise OSError("no xdg-open")
    monkeypatch.setattr(saves, "_launch", broken)
    assert client.post("/api/saved-comics/folder/open", headers=LOCAL).status_code == 400


def test_folder_routes_are_pc_only_and_reading_needs_permissions():
    from api.auth import iter_route_declarations
    from api.api_config import ApiSettings
    from api.server import create_app
    decls = {(path, tuple(sorted(methods))): d
             for _r, path, methods, d in iter_route_declarations(create_app(ApiSettings()))
             if path.startswith("/api/saved-comics")}
    assert decls[("/api/saved-comics/folder", ("GET",))] == [("local_only", None)]
    assert decls[("/api/saved-comics/folder", ("POST",))] == [("local_only", None)]
    assert decls[("/api/saved-comics/folder/open", ("POST",))] == [("local_only", None)]
    assert decls[("/api/saved-comics/series", ("GET",))] == [("permission", "library.read")]
    assert decls[("/api/saved-comics/page", ("GET",))] == [("permission", "media.stream")]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def test_series_and_chapters_in_natural_order(client, root):
    _library(root)
    os.makedirs(os.path.join(root, "MangaK", "Empty"))
    r = client.get("/api/saved-comics/series")
    assert r.status_code == 200 and root not in r.text
    [series] = r.json()
    assert (series["source"], series["series"], series["chapter_count"]) == ("MangaK", "Test Camp", 3)
    r = client.get("/api/saved-comics/chapters", params=_q())
    assert [(c["chapter"], c["title"], c["number"]) for c in r.json()["chapters"]] == [
        ("0001 Chapter 1", "Chapter 1", 1), ("0002 Chapter 2", "Chapter 2", 2),
        ("0010 Chapter 10", "Chapter 10", 10)]


def test_no_saves_yet_is_an_empty_library(client, root):
    assert client.get("/api/saved-comics/series").json() == []


def test_pages_skip_non_images_and_link_the_chapters_around(client, root):
    _library(root)
    r = client.get("/api/saved-comics/pages", params=_q(chapter="0010 Chapter 10"))
    body = r.json()
    assert r.status_code == 200 and root not in r.text
    assert [p["page"] for p in body["pages"]] == [1, 2, 3]
    # natural order: 1.png, 2.png, 010.png
    assert [p["width"] for p in body["pages"]] == [22, 21, 20]
    assert body["prev_chapter"] == "0002 Chapter 2" and body["next_chapter"] is None
    first = client.get("/api/saved-comics/pages", params=_q(chapter="0001 Chapter 1")).json()
    assert first["prev_chapter"] is None and first["next_chapter"] == "0002 Chapter 2"


def test_page_image_with_etag(client, root):
    _library(root)
    params = _q(chapter="0002 Chapter 2", page=2)
    r = client.get("/api/saved-comics/page", params=params)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    from PIL import Image
    assert Image.open(io.BytesIO(r.content)).size == (21, 30)
    again = client.get("/api/saved-comics/page", params=params,
                       headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    assert client.get("/api/saved-comics/page",
                      params=_q(chapter="0002 Chapter 2", page=3)).status_code == 404
    assert client.get("/api/saved-comics/page",
                      params=_q(chapter="0002 Chapter 2", page=0)).status_code == 422


def test_a_damaged_file_is_not_found(client, root):
    series = os.path.join(root, "MangaK", "Test Camp")
    os.makedirs(series)
    with open(os.path.join(series, "0001 Broken.cbz"), "wb") as fh:
        fh.write(b"not a zip")
    r = client.get("/api/saved-comics/pages", params=_q(chapter="0001 Broken"))
    assert r.status_code == 404 and root not in r.text


@pytest.mark.parametrize("params", [
    {"source": "..", "series": "Test Camp", "chapter": "0001 Chapter 1"},
    {"source": "MangaK", "series": "../MangaK/Test Camp", "chapter": "0001 Chapter 1"},
    {"source": "MangaK", "series": "Test Camp", "chapter": "../../secret"},
    {"source": "MangaK", "series": "Test Camp", "chapter": "a\\b"},
    {"source": "MangaK", "series": "Test Camp", "chapter": ".hidden"},
    {"source": "MangaK", "series": "Test Camp", "chapter": "x\x00y"},
    {"source": "D:Users", "series": "anna", "chapter": "0001 Chapter 1"},
    {"source": "MangaK", "series": "C:..", "chapter": "0001 Chapter 1"},
])
def test_names_that_are_not_one_plain_component_are_refused(client, root, params):
    _library(root)
    r = client.get("/api/saved-comics/pages", params=params)
    assert r.status_code in (404, 422) and root not in r.text
    r = client.get("/api/saved-comics/page", params={**params, "page": 1})
    assert r.status_code in (404, 422)


def test_a_path_on_another_drive_counts_as_outside_not_as_an_error(monkeypatch):
    from services import saved_comics_service as svc

    def other_drive(paths):
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(svc.os.path, "commonpath", other_drive)
    assert svc._inside("/saved", "/saved/x") is False


def test_a_link_out_of_the_save_folder_is_refused(client, root, tmp_path):
    outside = tmp_path / "outside"
    _cbz(str(outside / "0001 Secret.cbz"), ["001.png"])
    os.makedirs(os.path.join(root, "MangaK"))
    try:
        os.symlink(str(outside), os.path.join(root, "MangaK", "Linked"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    r = client.get("/api/saved-comics/pages",
                   params={"source": "MangaK", "series": "Linked", "chapter": "0001 Secret"})
    assert r.status_code == 404


def test_too_many_pages_is_refused(client, root, monkeypatch):
    _library(root)
    monkeypatch.setattr(library, "MAX_PAGES", 1)
    r = client.get("/api/saved-comics/pages", params=_q(chapter="0002 Chapter 2"))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Tracked series: save new chapters as CBZ
# ---------------------------------------------------------------------------

def _track(name):
    known = [c for c in _chapters(name, "s1") if c.chapter_id == "c1"]
    store.track_series(name, "s1", "Series T", "", None, known_chapters=known)


def test_tracked_save_switch(client, fakes, root):
    fakes["comicx"] = _make("comicx", comic=True)
    fakes["novelx"] = _make("novelx")
    body = {"source": "comicx", "series_id": "s1", "save_cbz": True}
    assert client.post("/api/sources/tracked/save-cbz", json=body).status_code == 404
    _track("comicx")
    _track("novelx")
    r = client.post("/api/sources/tracked/save-cbz", json=body)
    assert r.status_code == 200
    assert {(t["source"], t["save_cbz"]) for t in r.json()} == {("comicx", True), ("novelx", False)}
    r = client.post("/api/sources/tracked/save-cbz", json={**body, "save_cbz": False})
    assert all(t["save_cbz"] is False for t in r.json())
    assert client.post("/api/sources/tracked/save-cbz",
                       json={**body, "source": "novelx"}).status_code == 400
    assert client.post("/api/sources/tracked/save-cbz",
                       json={**body, "source": "nope"}).status_code == 404
    assert client.post("/api/sources/tracked/save-cbz",
                       json={**body, "save_cbz": "yes"}).status_code == 422


def test_check_saves_new_chapters_of_series_that_ask_for_it(fakes, root, monkeypatch):
    fakes["comicx"] = _make("comicx", comic=True)
    monkeypatch.setattr(registry, "is_enabled", lambda name: True)
    _track("comicx")
    summary = chapter_check.run_check_cycle(adapter_factory=lambda n: fakes["comicx"]())
    assert summary["new"] == 2 and summary["saved"] == [] and not os.path.exists(root)
    # new chapters again, now with saving on
    store.set_tracked_save("comicx", "s1", True)
    with store.connect() as conn:
        conn.execute("DELETE FROM known_chapters WHERE chapter_id != 'c1'")
    summary = chapter_check.run_check_cycle(adapter_factory=lambda n: fakes["comicx"]())
    assert summary["new"] == 2 and summary["saved"] == ["Series T"] and summary["errors"] == {}
    folder = os.path.join(root, "Comicx", "Series T [s1]")
    assert sorted(os.listdir(folder)) == ["0002 第2章.cbz", "0003 第10章.cbz"]


def test_a_failed_save_is_reported_and_the_chapters_stay_announced(fakes, root, monkeypatch):
    err = SourceError("site down", FailureReason.SERVER_ERROR)
    fakes["comicx"] = _make("comicx", comic=True, fail={"c2": err})
    monkeypatch.setattr(registry, "is_enabled", lambda name: True)
    _track("comicx")
    store.set_tracked_save("comicx", "s1", True)
    summary = chapter_check.run_check_cycle(adapter_factory=lambda n: fakes["comicx"]())
    assert summary["new"] == 2 and summary["saved"] == ["Series T"]
    assert summary["errors"]["Series T"].startswith("Saving as CBZ: 1 chapter(s) not saved")
    assert store.known_chapter_ids("comicx", "s1") == {"c1", "c2", "c10"}
