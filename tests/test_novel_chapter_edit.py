"""Renaming a saved novel chapter (services/novel_chapter_edit_service.py and
POST /api/novel/dramas/{id}/raw-novel/chapters/{n}/rename): the raw text, the chapter
list and the matching line change together. No network or models."""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import novel_chapter_edit_service as svc
from services import novel_chapters_service, novel_line_chapters_service
from sources import chapter_manifest, pipeline, store
from sources.models import ChapterInfo


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _import(did, n, title, body):
    ch = ChapterInfo(source="xbanxia", series_id="s1", chapter_id=f"c{n}", title=title)
    assert pipeline._append_chapter_text("xbanxia", ch, did, body) == ""
    # A finished import clears its retry marker; the marker is what a rename must refuse on.
    pipeline._clear_in_flight("xbanxia", ch, did)


def _book(isolated_db):
    did = db.create_drama(title_en="D")
    _import(did, 1, "The Gate", "他走了。\n\n门关了。")
    _import(did, 2, "The Road", "路很长。")
    _import(did, 3, "旅程", "夜深了。")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=t, en="e%d" % i)
                        for i, t in enumerate(["The Gate", "他走了。", "门关了。", "The Road",
                                               "路很长。", "旅程", "夜深了。"])])
    return did


def _raw(did):
    with open(chapter_manifest.raw_path(did), encoding="utf-8", newline="") as f:
        return f.read()


def test_rename_changes_file_manifest_and_line_and_keeps_later_chapters_readable(isolated_db):
    did = _book(isolated_db)
    out = svc.rename_chapter(did, 2, "A Longer Road Name")
    assert out["line_updated"] is True and out["title"] == "A Longer Road Name"
    assert "A Longer Road Name" in _raw(did) and "The Road" not in _raw(did)
    listing = novel_chapters_service.list_chapters(did)
    assert [c["title"] for c in listing["chapters"]] == ["The Gate", "A Longer Road Name", "旅程"]
    # Later chapters still start where the manifest says (the file grew by the title's delta).
    assert novel_chapters_service.read_chapter(did, 3)["text"].startswith("旅程")
    assert novel_chapters_service.read_chapter(did, 2)["text"].startswith("A Longer Road Name")
    mapped = novel_line_chapters_service.line_chapters(did)
    assert [(c["number"], c["title"], c["line_count"]) for c in mapped["chapters"]] == [
        (1, "The Gate", 3), (2, "A Longer Road Name", 2), (3, "旅程", 2)]
    # The translation of the title line is untouched; only its source text changed.
    assert db.load_lines(did)[3]["en"] == "e3"


def test_rename_keeps_a_backup_of_the_previous_files(isolated_db):
    did = _book(isolated_db)
    before = _raw(did)
    svc.rename_chapter(did, 1, "Gate")
    with open(chapter_manifest.raw_path(did) + svc.BACKUP_SUFFIX, encoding="utf-8", newline="") as f:
        assert f.read() == before
    assert os.path.exists(chapter_manifest.manifest_path(did) + svc.BACKUP_SUFFIX)


def test_refusals_leave_everything_as_it_was(isolated_db):
    did = _book(isolated_db)
    before = _raw(did)
    with pytest.raises(NotFoundError):
        svc.rename_chapter(did, 9, "X")
    with pytest.raises(InvalidInputError):
        svc.rename_chapter(did, 1, "   ")
    plain = db.create_drama(title_en="P")
    db.drama_dir(plain)
    with open(chapter_manifest.raw_path(plain), "w", encoding="utf-8") as f:
        f.write("没有章节列表。")
    with pytest.raises(ConflictError):
        svc.rename_chapter(plain, 1, "X")
    assert _raw(did) == before


def test_a_running_job_blocks_the_rename(isolated_db, monkeypatch):
    did = _book(isolated_db)
    monkeypatch.setattr(svc.drama_service, "job_running_for_drama", lambda *a, **k: True)
    with pytest.raises(ConflictError):
        svc.rename_chapter(did, 1, "X")


def test_route_renames(client, isolated_db):
    did = _book(isolated_db)
    r = client.post(f"/api/novel/dramas/{did}/raw-novel/chapters/1/rename", json={"title": "Gate"})
    assert r.status_code == 200 and r.json()["title"] == "Gate"
    assert client.post(f"/api/novel/dramas/{did}/raw-novel/chapters/9/rename",
                       json={"title": "Gate"}).status_code == 404


def _state(did):
    return (_raw(did), open(chapter_manifest.manifest_path(did), encoding="utf-8").read(),
            [r["zh"] for r in db.load_lines(did)])


def test_a_failed_manifest_swap_restores_the_raw_file_and_the_line(isolated_db, monkeypatch):
    did = _book(isolated_db)
    before = _state(did)
    real = svc._swap_in
    calls = []

    def flaky(tmp, dest):
        calls.append(dest)
        if dest.endswith(chapter_manifest.MANIFEST_FILENAME):
            raise OSError("disk")
        return real(tmp, dest)
    monkeypatch.setattr(svc, "_swap_in", flaky)
    with pytest.raises(OSError):
        svc.rename_chapter(did, 2, "A Longer Road Name")
    monkeypatch.setattr(svc, "_swap_in", real)
    assert _state(did) == before
    assert novel_chapters_service.list_chapters(did)["split"] is True
    assert not [n for n in os.listdir(os.path.dirname(chapter_manifest.raw_path(did))) if n.endswith(".part")]
    svc.rename_chapter(did, 2, "A Longer Road Name")   # a retry finishes it
    assert novel_chapters_service.list_chapters(did)["chapters"][1]["title"] == "A Longer Road Name"


def test_a_failed_line_save_changes_nothing(isolated_db, monkeypatch):
    did = _book(isolated_db)
    before = _state(did)

    def boom(*a, **k):
        raise RuntimeError("db")
    monkeypatch.setattr(db, "save_lines", boom)
    with pytest.raises(RuntimeError):
        svc.rename_chapter(did, 2, "New")
    monkeypatch.undo()
    assert _state(did) == before


def test_a_pending_import_retry_blocks_the_rename(isolated_db):
    did = _book(isolated_db)
    store.mark_text_in_flight("xbanxia", "s1", did, "c9", "Later", "Interrupted.", 123)
    before = _state(did)
    with pytest.raises(ConflictError):
        svc.rename_chapter(did, 2, "New")
    assert _state(did) == before


def test_renaming_to_the_same_title_keeps_the_only_backup(isolated_db):
    did = _book(isolated_db)
    svc.rename_chapter(did, 1, "Gate")
    backup = chapter_manifest.raw_path(did) + svc.BACKUP_SUFFIX
    with open(backup, "rb") as f:
        first_backup = f.read()
    out = svc.rename_chapter(did, 1, "Gate")
    assert out["line_updated"] is False
    with open(backup, "rb") as f:
        assert f.read() == first_backup


def test_a_translation_text_that_copies_the_raw_chapters_follows_the_rename(isolated_db):
    did = _book(isolated_db)
    folder = os.path.dirname(chapter_manifest.raw_path(did))
    copy = os.path.join(folder, "novel_narration_source.txt")
    with open(chapter_manifest.raw_path(did), "rb") as f, open(copy, "wb") as g:
        g.write(f.read())
    assert svc.rename_chapter(did, 1, "Gate")["translation_text_updated"] is True
    with open(copy, encoding="utf-8", newline="") as f:
        assert f.read() == _raw(did)
    with open(copy, "a", encoding="utf-8") as f:   # a hand edit is never overwritten
        f.write("\n手改")
    assert svc.rename_chapter(did, 2, "Road")["translation_text_updated"] is False
    assert "手改" in open(copy, encoding="utf-8").read()
