"""Tests for services/title_copy_service.py: an independent copy of a title."""
import contextlib
import os

import pytest

import db
from core import Line
from lib.errors import ConflictError, NotFoundError
from services import drama_service, title_copy_service as tcs


def _source(isolated_db):
    series = db.create_series("S")
    did = db.create_drama(title_en="Show", title_zh="剧", source_language="zh",
                          custom_tags="Hot", audio_filename="a.mp3",
                          source_url="https://example.com/x", episode_number=3,
                          series_id=series)
    folder = db.drama_dir(did)
    with open(os.path.join(folder, "a.mp3"), "wb") as f:
        f.write(b"audio-bytes")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hi", speaker="A", reviewed=True),
                        Line(idx=1, start=1, end=2, zh="再见", en="Bye", speaker="B")])
    db.upsert_character(did, "A", "Ann")
    return did, series


def test_copy_has_own_rows_and_files(isolated_db):
    did, series = _source(isolated_db)
    copy = tcs.duplicate_title(did)
    new_id = copy["id"]
    assert new_id != did and copy["title_en"] == "Show (copy)"
    row = db.get_drama(new_id)
    assert row["audio_filename"] == "a.mp3" and row["series_id"] == series
    assert "Test copy" in row["custom_tags"] and "Hot" in row["custom_tags"]
    assert not row["source_url"] and not row["episode_number"]
    lines = db.load_lines(new_id)
    assert [(l["zh"], l["en"], l["reviewed"]) for l in lines] == [("你好", "Hi", 1), ("再见", "Bye", 0)]
    assert {l["id"] for l in lines}.isdisjoint({l["id"] for l in db.load_lines(did)})
    assert [c["character_name"] for c in db.list_characters(new_id)] == ["Ann"]
    new_file = os.path.join(db.DRAMAS_DIR, str(new_id), "a.mp3")
    assert open(new_file, "rb").read() == b"audio-bytes"
    assert not os.path.samefile(new_file, os.path.join(db.DRAMAS_DIR, str(did), "a.mp3"))


def test_copy_carries_comic_text_boxes(isolated_db):
    did, _ = _source(isolated_db)
    with contextlib.closing(db.get_conn()) as conn:
        for idx in range(2):
            cur = conn.execute("INSERT INTO pages (drama_id, idx, filename) VALUES (?, ?, ?)",
                               (did, idx, f"p{idx}.png"))
            conn.execute("INSERT INTO bubbles (page_id, idx, x, y, w, h, source_text, translated_text, skip) "
                         "VALUES (?, 0, 1, 2, 3, 4, ?, ?, ?)", (cur.lastrowid, f"原{idx}", f"en{idx}", idx))
        conn.commit()
    new_id = tcs.duplicate_title(did)["id"]
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT p.idx, b.source_text, b.translated_text, b.skip, b.w FROM bubbles b "
                            "JOIN pages p ON p.id = b.page_id WHERE p.drama_id = ? ORDER BY p.idx",
                            (new_id,)).fetchall()
        original = conn.execute("SELECT COUNT(*) FROM bubbles b JOIN pages p ON p.id = b.page_id "
                                "WHERE p.drama_id = ?", (did,)).fetchone()[0]
    assert [tuple(r) for r in rows] == [(0, "原0", "en0", 0, 3), (1, "原1", "en1", 1, 3)]
    assert original == 2


def test_editing_the_copy_leaves_the_original(isolated_db):
    did, _ = _source(isolated_db)
    new_id = tcs.duplicate_title(did)["id"]
    lines = [Line(idx=0, start=0, end=1, zh="x", en="changed")]
    db.save_lines(new_id, lines)
    with open(os.path.join(db.DRAMAS_DIR, str(new_id), "a.mp3"), "wb") as f:
        f.write(b"other")
    assert [l["en"] for l in db.load_lines(did)] == ["Hi", "Bye"]
    assert open(os.path.join(db.DRAMAS_DIR, str(did), "a.mp3"), "rb").read() == b"audio-bytes"
    drama_service.hard_delete_drama(new_id)
    assert len(db.load_lines(did)) == 2
    assert os.path.exists(os.path.join(db.DRAMAS_DIR, str(did), "a.mp3"))


def test_refused_while_job_runs(isolated_db, monkeypatch):
    did, _ = _source(isolated_db)
    monkeypatch.setattr(drama_service, "job_running_for_drama", lambda _id: True)
    with pytest.raises(ConflictError):
        tcs.duplicate_title(did)
    assert len(db.list_dramas()) == 1


def test_failed_copy_leaves_no_title(isolated_db, monkeypatch):
    did, _ = _source(isolated_db)

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(tcs.shutil, "copytree", boom)
    with pytest.raises(OSError):
        tcs.duplicate_title(did)
    assert [d["id"] for d in db.list_dramas()] == [did]


def test_missing_title(isolated_db):
    with pytest.raises(NotFoundError):
        tcs.duplicate_title(999)
