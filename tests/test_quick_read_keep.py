"""Debug copies of Quick read pages."""

import json
import os
import time

import pytest

from services import quick_read_keep


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(quick_read_keep, "_folder", lambda: str(tmp_path))
    return tmp_path


def test_write_keeps_picture_and_both_region_sets(folder):
    quick_read_keep._write("page-1", b"PIC", "image/png", [{"x": 1, "source_text": "원문"}],
                           {"width": 10, "height": 20, "regions": [{"translated_text": "Hi"}]})
    [meta] = [f for f in os.listdir(folder) if f.endswith(".json")]
    data = json.loads((folder / meta).read_text(encoding="utf-8"))
    assert data["read_regions"][0]["source_text"] == "원문"
    assert data["regions"][0]["translated_text"] == "Hi"
    assert (folder / data["image"]).read_bytes() == b"PIC"


def test_sweep_keeps_newest_pages_and_drops_old_ones(folder, monkeypatch):
    monkeypatch.setattr(quick_read_keep, "MAX_PAGES", 2)
    for n in range(4):
        quick_read_keep._write(f"p{n}", b"x", "image/jpeg", [], {})
        time.sleep(0.01)
    quick_read_keep._sweep()
    assert len([f for f in os.listdir(folder) if f.endswith(".json")]) == 2
    assert len([f for f in os.listdir(folder) if f.endswith(".jpg")]) == 2
    old = [f for f in os.listdir(folder) if f.endswith(".json")][0]
    os.utime(folder / old, (1, 1))
    quick_read_keep._sweep()
    assert len([f for f in os.listdir(folder) if f.endswith(".json")]) == 1


def test_note_read_is_taken_once_and_json_safe(folder):
    import numpy as np
    quick_read_keep.note_read([{"x": np.int64(3), "box": np.array([1, 2])}])
    assert quick_read_keep.take() == [{"x": 3, "box": [1, 2]}]
    assert quick_read_keep.take() is None


def test_off_by_default(isolated_db, monkeypatch):
    monkeypatch.setattr(quick_read_keep, "_enabled_at", 0.0)
    assert quick_read_keep.enabled() is False


def test_backups_leave_the_copies_out_and_a_restore_keeps_the_current_ones(isolated_db, tmp_path):
    import zipfile
    import db
    from services import library_backup_service as lbs
    from services import library_restore_service as lrs
    os.makedirs(os.path.join(db.LIBRARY_DIR, quick_read_keep.DIRNAME))
    with open(os.path.join(db.LIBRARY_DIR, quick_read_keep.DIRNAME, "page.jpg"), "wb") as f:
        f.write(b"PIC")
    dest = str(tmp_path / "b.zip")
    lbs.write_backup_zip(dest, include_media=True)
    assert not any(n.split("/")[0] == quick_read_keep.DIRNAME for n in zipfile.ZipFile(dest).namelist())
    assert quick_read_keep.DIRNAME in lrs.restore_kept_names()


def test_a_page_never_keeps_the_read_regions_of_a_page_that_failed_before_it(monkeypatch):
    from types import SimpleNamespace
    from services import extension_bridge_service as bridge
    from services import page_server
    kept = []

    def translate(content, content_type, **kwargs):
        if content == b"first":
            quick_read_keep.note_read([{"source_text": "first page"}])
            raise page_server.EndpointError(500, "the engine failed")
        return {"regions": [], "seconds": {}}
    monkeypatch.setattr(page_server, "translate_image", translate)
    monkeypatch.setattr(quick_read_keep, "enabled", lambda: True)
    monkeypatch.setattr(quick_read_keep, "save", lambda key, original, content_type, read, result: kept.append(read))
    job = {"flat_pages": SimpleNamespace(undrawn=lambda image: False), "drama_id": None, "source_url": "",
           "source_language": "", "store": False, "member": False, "typeset": False, "typeset_style": None,
           "typeset_max_width": None, "single": False}
    for content in (b"first", b"second"):
        bridge._translate_fresh({"key": "p1", "url": "u", "content": content, "content_type": "image/png"},
                                job, None, None, time.monotonic())
    assert kept == [None]
