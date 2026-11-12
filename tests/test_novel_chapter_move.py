"""Moving a saved novel chapter (services/novel_chapter_move_service.py and
POST /api/novel/dramas/{id}/raw-novel/chapters/{n}/move): the raw text, the chapter list and the
chapter's lines move together and keep their translations. No network or models."""

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
from services import novel_chapter_move_service as svc
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
    pipeline._clear_in_flight("xbanxia", ch, did)


TEXTS = ["One", "甲。", "乙。", "Two", "丙。", "Three", "丁。", "戊。"]


def _book(isolated_db):
    did = db.create_drama(title_en="D")
    _import(did, 1, "One", "甲。\n\n乙。")
    _import(did, 2, "Two", "丙。")
    _import(did, 3, "Three", "丁。\n\n戊。")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=t, en=f"en{i}")
                        for i, t in enumerate(TEXTS)])
    return did


def _raw(did):
    with open(chapter_manifest.raw_path(did), encoding="utf-8", newline="") as f:
        return f.read()


def _state(did):
    with open(chapter_manifest.manifest_path(did), encoding="utf-8") as f:
        return _raw(did), f.read(), [(r["id"], r["idx"], r["zh"], r["en"]) for r in db.load_lines(did)]


def _state_lines(did):
    return [(r["id"], r["idx"], r["zh"], r["en"]) for r in db.load_lines(did)]


def test_moving_a_chapter_reorders_text_list_and_lines_and_keeps_translations(isolated_db):
    did = _book(isolated_db)
    before_ids = {r["zh"]: (r["id"], r["en"]) for r in db.load_lines(did)}
    out = svc.move_chapter(did, 3, 1)
    assert out["lines_moved"] == 8
    titles = [c["title"] for c in novel_chapters_service.list_chapters(did)["chapters"]]
    assert titles == ["Three", "One", "Two"]
    assert _raw(did).startswith("Three")
    assert novel_chapters_service.read_chapter(did, 3)["text"].startswith("Two")
    rows = db.load_lines(did)
    assert [r["zh"] for r in rows] == ["Three", "丁。", "戊。", "One", "甲。", "乙。", "Two", "丙。"]
    assert [r["idx"] for r in rows] == list(range(8))
    assert [(r["start"], r["end"]) for r in rows] == [(float(i), i + 1.0) for i in range(8)]
    for r in rows:   # the same rows, with their own translations
        assert (r["id"], r["en"]) == before_ids[r["zh"]]
    mapped = novel_line_chapters_service.line_chapters(did)["chapters"]
    assert [(c["number"], c["title"], c["line_count"]) for c in mapped] == [
        (1, "Three", 3), (2, "One", 3), (3, "Two", 2)]


def test_a_failed_file_swap_puts_the_lines_back(isolated_db, monkeypatch):
    did = _book(isolated_db)
    before = _state(did)

    real, calls = svc.edit._swap_in, []

    def first_fails(tmp, dest):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk")
        return real(tmp, dest)
    monkeypatch.setattr(svc.edit, "_swap_in", first_fails)
    with pytest.raises(OSError):
        svc.move_chapter(did, 2, 1)
    monkeypatch.undo()
    assert _state(did) == before


def test_a_failed_manifest_swap_restores_the_raw_text_too(isolated_db, monkeypatch):
    did = _book(isolated_db)
    before = _state(did)
    real = svc.edit._swap_in
    calls = []

    def second_fails(tmp, dest):
        calls.append(dest)
        if len(calls) == 2:
            raise OSError("disk")
        return real(tmp, dest)
    monkeypatch.setattr(svc.edit, "_swap_in", second_fails)
    with pytest.raises(OSError):
        svc.move_chapter(did, 2, 1)
    monkeypatch.undo()
    assert _state(did) == before


def test_a_move_that_cannot_put_the_lines_back_says_where_they_are_saved(isolated_db, monkeypatch):
    did = _book(isolated_db)
    real_save = db.save_lines
    saves = []

    def second_fails(*a, **k):
        saves.append(1)
        if len(saves) > 1:
            raise RuntimeError("db")
        return real_save(*a, **k)
    monkeypatch.setattr(db, "save_lines", second_fails)
    monkeypatch.setattr(svc.edit, "_swap_in", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(ConflictError) as err:
        svc.move_chapter(did, 2, 1)
    assert "line history" in err.value.message


def test_lines_that_do_not_line_up_refuse_the_move(isolated_db):
    did = _book(isolated_db)
    rows = db.load_lines(did)
    rows[3]["zh"] = "Two (edited)"
    db.save_lines(did, [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"], en=r["en"],
                             id=r["id"]) for r in rows])
    before = _state(did)
    with pytest.raises(ConflictError):
        svc.move_chapter(did, 3, 1)
    assert _state(did) == before


def _rewrite_lines(did, texts, timed=False):
    db.save_lines(did, [Line(idx=r["idx"], start=r["start"] + (0.5 if timed else 0), end=r["end"],
                             zh=t, en=r["en"], id=r["id"])
                        for r, t in zip(db.load_lines(did), texts)], fields=("zh", "start", "end"))


def test_a_body_line_equal_to_a_later_title_refuses_instead_of_cutting_early(isolated_db):
    did = db.create_drama(title_en="D")
    _import(did, 1, "上", "甲。\n\n下\n\n乙。")
    _import(did, 2, "下", "丙。")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=t, en=f"en{i}")
                        for i, t in enumerate(["上", "甲。", "下", "乙。", "下", "丙。"])])
    before = _state(did)
    with pytest.raises(ConflictError):
        svc.move_chapter(did, 2, 1)
    assert _state(did) == before


def test_lines_with_their_own_times_refuse_the_move(isolated_db):
    did = _book(isolated_db)
    _rewrite_lines(did, TEXTS, timed=True)
    before = _state(did)
    with pytest.raises(ConflictError) as err:
        svc.move_chapter(did, 3, 1)
    assert "times" in err.value.message
    assert _state(did) == before


def test_a_raw_file_that_cannot_be_restored_still_gets_the_lines_put_back(isolated_db, monkeypatch):
    did = _book(isolated_db)
    before_lines = _state(did)[2]
    calls = []
    real = svc.edit._swap_in

    def second_fails(tmp, dest):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk")
        return real(tmp, dest)
    monkeypatch.setattr(svc.edit, "_swap_in", second_fails)
    monkeypatch.setattr(svc.edit, "_restore_raw", lambda *a: (_ for _ in ()).throw(OSError("gone")))
    with pytest.raises(ConflictError) as err:
        svc.move_chapter(did, 2, 1)
    monkeypatch.undo()
    assert ".before-edit" in err.value.message
    assert _state_lines(did) == before_lines
    # The old chapter list must not stay valid over the moved text: no list reads as unsplit text.
    assert not os.path.exists(chapter_manifest.manifest_path(did))
    assert novel_chapters_service.list_chapters(did)["split"] is False


def test_refusals(isolated_db):
    did = _book(isolated_db)
    with pytest.raises(NotFoundError):
        svc.move_chapter(did, 9, 1)
    with pytest.raises(InvalidInputError):
        svc.move_chapter(did, 2, 2)
    store.mark_text_in_flight("xbanxia", "s1", did, "c9", "Later", "Interrupted.", 5)
    with pytest.raises(ConflictError):
        svc.move_chapter(did, 3, 1)


def test_a_translation_text_that_copies_the_raw_chapters_follows_the_move(isolated_db):
    did = _book(isolated_db)
    copy = os.path.join(os.path.dirname(chapter_manifest.raw_path(did)), "novel_narration_source.txt")
    with open(chapter_manifest.raw_path(did), "rb") as f, open(copy, "wb") as g:
        g.write(f.read())
    assert svc.move_chapter(did, 3, 1)["translation_text_updated"] is True
    with open(copy, encoding="utf-8", newline="") as f:
        assert f.read() == _raw(did)


def test_a_translation_text_that_is_not_a_copy_is_left_alone_and_reported(isolated_db):
    did = _book(isolated_db)
    copy = os.path.join(os.path.dirname(chapter_manifest.raw_path(did)), "novel_narration_source.txt")
    with open(copy, "w", encoding="utf-8") as g:
        g.write("pasted and edited")
    out = svc.move_chapter(did, 3, 1)
    assert (out["translation_text_updated"], out["translation_text_stale"]) == (False, True)
    with open(copy, encoding="utf-8") as f:
        assert f.read() == "pasted and edited"


def test_route_moves_a_chapter(client, isolated_db):
    did = _book(isolated_db)
    r = client.post(f"/api/novel/dramas/{did}/raw-novel/chapters/2/move", json={"to_number": 1})
    assert r.status_code == 200 and r.json()["lines_moved"] == 8
    assert client.post(f"/api/novel/dramas/{did}/raw-novel/chapters/2/move",
                       json={"to_number": 9}).status_code == 404
